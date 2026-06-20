# -*- coding: utf-8 -*-
"""
Generic ORM executor with allowlist enforcement and env.ref() resolution.

Receives signed instruction sets → validates against model.method allowlist →
resolves XML ID references → executes via Odoo ORM reflection.

Key guarantees:
- NO instruction executes without allowlist check (even with valid HMAC)
- env.ref() resolved for args containing XML ID strings
- All results serialized via proper serializer (datetime, binary, recordset safe)
- Multi-step instructions support transactional rollback via savepoints
- Many2one values normalized (lists, tuples, recordsets → single ID)
- ISO 8601 datetime strings converted to Odoo format before dispatch
- Step references support list indexing (${step.0.field}) and dict unwrapping
"""

import logging
import re
import time

from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from odoo import models

from .allowlist import is_allowed
from .serializer import serialize_result

_logger = logging.getLogger(__name__)

# -- helpers --

def _safe_repr(obj, max_len=120):
    """Safe repr of args/kwargs for log messages — truncates long values."""
    s = repr(obj)
    if len(s) > max_len:
        return s[:max_len] + '…'
    return s

# Regex for valid Odoo XML IDs: module_name.model_id or module.xml_id
_XML_ID_RE = re.compile(r'^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$')

# ISO 8601 datetime pattern: 2026-06-12T13:52:32 or 2026-06-12T13:52:32.123456
# Also matches timezone-aware: 2026-06-12T13:52:32Z or +00:00/-05:00
_ISO_DATETIME_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$'
)


def _resolve_xml_ref(env, value):
    """Resolve an XML ID string to its database ID via env.ref().

    Only attempts resolution for strings matching the strict XML ID pattern
    (module.xml_id) to avoid false positives on URLs, version strings, and
    other dot-containing values.

    Args:
        env: Odoo Environment
        value: str in format 'module.xml_id' (e.g. 'mail.mail_activity_data_meeting')

    Returns:
        int: database ID, or the original value if not an XML ID string
    """
    if isinstance(value, str) and _XML_ID_RE.match(value) and not value.startswith('$'):
        try:
            return env.ref(value).id
        except ValueError:
            return value
    return value


def _resolve_xml_refs(env, data):
    """Recursively resolve XML ID references in args and kwargs."""
    if isinstance(data, dict):
        return {k: _resolve_xml_refs(env, v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_xml_refs(env, v) for v in data]
    return _resolve_xml_ref(env, data)


def _normalize_m2o_values(vals):
    """Normalize Many2one field values in a write/create vals dict.

    Odoo's crm.lead.write() calls browse(vals['stage_id']) which crashes if
    the value is a list (→ multi-record), tuple (→ unpacked as multiple IDs),
    recordset (→ can't adapt type), dict (→ browse uses string keys as _ids),
    or list-of-dicts (→ browse iterates all records). This normalizes all
    cases to a single integer ID.

    Args:
        vals: dict of field→value pairs for write/create

    Returns:
        dict with normalized Many2one values
    """
    for key, value in vals.items():
        # List: extract first element (list of int IDs or list of dicts)
        if isinstance(value, list):
            if value:
                if isinstance(value[0], int):
                    vals[key] = value[0]
                elif isinstance(value[0], dict) and 'id' in value[0]:
                    vals[key] = value[0]['id']
        # Dict (serialized single record, e.g. from ${step.0}): extract 'id'
        elif isinstance(value, dict):
            if 'id' in value:
                vals[key] = value['id']
        # Tuple: extract first element (the ID)
        elif isinstance(value, tuple):
            if value:
                vals[key] = value[0]
        # Recordset: extract .id
        elif isinstance(value, models.BaseModel):
            if value:
                vals[key] = value.id
    return vals


def _normalize_datetime_strings(vals):
    """Convert ISO 8601 datetime strings in vals to Odoo's expected format.

    Odoo's calendar.event and other models expect '%Y-%m-%d %H:%M:%S' format,
    but the gateway sends ISO 8601 ('2026-06-12T13:52:32'). This converts
    ISO strings to Odoo format before dispatch.

    Args:
        vals: dict of field→value pairs for write/create

    Returns:
        dict with datetime strings converted
    """
    for key, value in vals.items():
        if isinstance(value, str) and _ISO_DATETIME_RE.match(value):
            # Convert '2026-06-12T13:52:32.123456' → '2026-06-12 13:52:32'
            vals[key] = value.replace('T', ' ').split('.')[0]
    return vals


def execute_instruction(env, instruction):
    """Execute a single ORM instruction with allowlist check.

    Steps:
      1. Check model.method against allowlist → reject if not allowed
      2. Resolve XML ID references in args/kwargs
      3. Normalize Many2one values (lists, tuples, recordsets → single ID)
      4. Convert ISO datetime strings to Odoo format
      5. Resolve model via Odoo registry
      6. Apply context/sudo as requested
      7. Browse record IDs if provided
      8. Reflect method and execute
      9. Serialize result to JSON-safe format
    """
    model_name = instruction.get('model', '')
    method_name = instruction.get('method', '')

    # --- STEP 0: Allowlist check (defense-in-depth) ---
    if not is_allowed(model_name, method_name):
        _logger.warning("BLOCKED: %s.%s — not in allowlist", model_name, method_name)
        return {
            'success': False,
            'error': f"Operation not allowed: {model_name}.{method_name}",
            'blocked': True,
        }

    record_ids = instruction.get('ids')
    args = instruction.get('args', [])
    kwargs = instruction.get('kwargs', {})
    use_sudo = instruction.get('sudo', False)
    context = instruction.get('context', {})

    # --- STEP 1: Resolve XML ID references ---
    # Some methods (e.g. activity_schedule) handle their own XML ID
    # resolution internally and will crash if passed a pre-resolved
    # integer instead of the raw XML ID string.
    if instruction.get('resolve_xml_refs', True):
        args = _resolve_xml_refs(env, args)
        kwargs = _resolve_xml_refs(env, kwargs)

    # --- STEP 2: Normalize Many2one values (prevent list/tuple/recordset crashes) ---
    if method_name == 'write' and args:
        if isinstance(args[0], dict):
            args[0] = _normalize_m2o_values(args[0])
        elif isinstance(args[0], list) and args[0] and isinstance(args[0][0], dict):
            args[0] = [_normalize_m2o_values(d) for d in args[0]]
    if method_name == 'create' and args:
        if isinstance(args[0], dict):
            args[0] = _normalize_m2o_values(args[0])
        elif isinstance(args[0], list) and args[0] and isinstance(args[0][0], dict):
            args[0] = [_normalize_m2o_values(d) for d in args[0]]

    # --- STEP 3: Convert ISO datetime strings to Odoo format ---
    if method_name in ('create', 'write') and args:
        if isinstance(args[0], dict):
            args[0] = _normalize_datetime_strings(args[0])
        elif isinstance(args[0], list) and args[0] and isinstance(args[0][0], dict):
            args[0] = [_normalize_datetime_strings(d) for d in args[0]]

    _logger.debug("Executing: %s.%s(sudo=%s)", model_name, method_name, use_sudo)

    try:
        # --- STEP 4: Resolve model ---
        model = env[model_name]

        # --- STEP 5: Apply context and sudo ---
        if context:
            model = model.with_context(**context)
        if use_sudo:
            model = model.sudo()

        # --- STEP 6: Browse records ---
        if record_ids:
            model = model.browse(record_ids)
            if not model.exists():
                _logger.warning(
                    "Step failed: %s.%s — no %s records with IDs %s",
                    model_name, method_name, model_name, record_ids,
                )
                return {
                    'success': False,
                    'error': f"No {model_name} records found with IDs {record_ids}",
                }

        # --- STEP 7a: Resolve method (AttributeError here = method genuinely missing) ---
        try:
            method = getattr(model, method_name)
        except AttributeError:
            _logger.error(
                "Method not found: %s.%s (model=%s, allowlist=%s)",
                model_name, method_name, type(model).__name__,
                is_allowed(model_name, method_name),
            )
            return {
                'success': False,
                'error': f"Model '{model_name}' has no method '{method_name}'",
            }

        # --- STEP 7b: Execute (exceptions here = method crashed, not missing) ---
        try:
            result = method(*args, **kwargs)
        except AccessError as e:
            _logger.warning(
                "Access denied: %s.%s(args=%s, kwargs=%s) → %s",
                model_name, method_name, _safe_repr(args), _safe_repr(kwargs), e,
            )
            return {'success': False, 'error': f"Permission denied: {str(e)}"}
        except ValidationError as e:
            _logger.warning(
                "Validation error: %s.%s(args=%s, kwargs=%s) → %s",
                model_name, method_name, _safe_repr(args), _safe_repr(kwargs), e,
            )
            return {'success': False, 'error': str(e)}
        except AttributeError as e:
            _logger.error(
                "AttributeError in %s.%s(args=%s, kwargs=%s): %s",
                model_name, method_name, _safe_repr(args), _safe_repr(kwargs), e,
            )
            return {
                'success': False,
                'error': (
                    f"Method '{method_name}' on '{model_name}' raised AttributeError. "
                    f"This usually means an argument was the wrong type (e.g., "
                    f"an integer was passed where a string XML ID was expected). "
                    f"Detail: {e}"
                ),
            }
        except Exception as e:
            _logger.exception(
                "Error in %s.%s(args=%s, kwargs=%s): %s",
                model_name, method_name, _safe_repr(args), _safe_repr(kwargs), e,
            )
            return {'success': False, 'error': f"Execution error: {str(e)}"}

        # --- STEP 8: Collect notification data for mutating operations ---
        notification = _build_notification_data(
            env, model_name, method_name, record_ids
        )

        # --- STEP 9: Serialize ---
        serialized = serialize_result(result)
        response = {'success': True, 'result': serialized}
        if notification:
            response['_notification'] = notification
        return response

    except KeyError:
        _logger.error("Unknown model: %s (requested by instruction)", model_name)
        return {'success': False, 'error': f"Unknown model: {model_name}"}


def execute_instruction_set(env, instruction_set):
    """Execute a set of instructions, optionally within a transaction."""
    trace_id = instruction_set.get('trace_id', 'unknown')
    transactional = instruction_set.get('transaction', False)
    steps = instruction_set.get('steps', [])

    if not steps:
        return {'success': True, 'trace_id': trace_id, 'results': []}

    captured = {}
    results = []
    pending_notifications = []
    error_step = None

    if transactional:
        env.cr.execute('SAVEPOINT crm_assistant_instruction_set')

    total_start = time.time()
    try:
        for step in steps:
            step_start = time.time()
            step_id = step.get('id', f'step_{len(results)}')
            resolved_kwargs = _resolve_references(step.get('kwargs', {}), captured)
            resolved_args = _resolve_references(step.get('args', []), captured)
            resolved_ids = _resolve_references_list(step.get('ids'), captured)

            instruction = {
                'model': step.get('model'),
                'method': step.get('method'),
                'ids': resolved_ids,
                'args': resolved_args,
                'kwargs': resolved_kwargs,
                'sudo': step.get('sudo', False),
                'context': step.get('context', {}),
                'resolve_xml_refs': step.get('resolve_xml_refs', True),
            }

            _logger.info(
                "[%s] %s.%s ids=%s args=%s kwargs=%s",
                step_id, instruction['model'], instruction['method'],
                instruction['ids'], _safe_repr(instruction['args'], 80),
                _safe_repr(instruction['kwargs'], 80),
            )

            result = execute_instruction(env, instruction)
            step_ms = int((time.time() - step_start) * 1000)

            if not result['success']:
                error_step = step_id
                _logger.error(
                    "[%s] FAILED (%dms): %s",
                    step_id, step_ms, result.get('error', 'unknown error'),
                )
                results.append({'step': step_id, 'error': result['error']})
                break

            if step.get('capture_result'):
                captured[step_id] = result['result']

            results.append({'step': step_id, 'result': result['result']})
            _logger.info("[%s] OK (%dms)", step_id, step_ms)

            # Collect notification data (dispatch deferred for transactional sets)
            notification = result.get('_notification')
            if notification:
                if transactional:
                    pending_notifications.append(notification)
                else:
                    _dispatch_notification(env, notification)

        total_ms = int((time.time() - total_start) * 1000)

        if error_step and transactional:
            env.cr.execute('ROLLBACK TO SAVEPOINT crm_assistant_instruction_set')
            _logger.warning(
                "[%s] Rolled back at step '%s' (%dms total)",
                trace_id, error_step, total_ms,
            )
            pending_notifications = []  # discard — data was rolled back
        elif transactional:
            env.cr.execute('RELEASE SAVEPOINT crm_assistant_instruction_set')
            # Now safe to dispatch — transaction committed
            for notification in pending_notifications:
                _dispatch_notification(env, notification)

        if not error_step:
            _logger.info(
                "[%s] All %d steps OK (%dms total)",
                trace_id, len(results), total_ms,
            )

        return {
            'success': error_step is None,
            'trace_id': trace_id,
            'results': results,
            'error_step': error_step,
            'had_writes': len(pending_notifications) > 0,
        }

    except Exception as e:
        if transactional:
            try:
                env.cr.execute('ROLLBACK TO SAVEPOINT crm_assistant_instruction_set')
            except Exception:
                pass
        _logger.exception("[%s] Fatal error in instruction set: %s", trace_id, e)
        return {'success': False, 'trace_id': trace_id, 'error': str(e)}


def _build_notification_data(env, model_name, method_name, record_ids):
    """Build notification payload for successful write/create/unlink.

    Returns a dict with model, record_ids, and method if the operation
    is a mutating write — otherwise returns None. The caller is responsible
    for dispatching at the right time (e.g. after transaction commit).
    """
    if method_name not in ('write', 'create', 'unlink', 'action_set_won',
                            'action_set_lost', 'message_post', 'activity_schedule',
                            'action_cancel'):
        return None
    return {
        'model': model_name,
        'record_ids': list(record_ids) if isinstance(record_ids, (list, tuple)) else [],
        'method': method_name,
    }


def _dispatch_notification(env, notification):
    """Dispatch a single bus notification for a record change.

    Sends on the crm_assistant_{userId} channel. If the bus module is
    not installed or dispatch fails, logs a warning but does not raise.
    """
    try:
        env['bus.bus']._sendone(
            f'crm_assistant_{env.uid}',
            'crm_assistant_record_changed',
            notification,
        )
        _logger.info(
            "Bus notification dispatched: %s.%s on channel crm_assistant_%s",
            notification['model'], notification['method'], env.uid,
        )
    except Exception as e:
        _logger.warning(
            "Failed to dispatch bus notification for %s.%s: %s",
            notification['model'], notification['method'], e,
        )


def _resolve_references(data, captured):
    """Resolve ${step_id}, ${step_id.field}, and ${step_id.N.field} references.

    Supports:
      - ${step_id} → captured value directly
      - ${step_id.field} → captured dict's field (e.g. captured['id'])
      - ${step_id.N.field} → captured list's N-th element's field
        (e.g. captured[0]['id'] for search_read results)

    Recurses into nested structures so references inside vals dicts in args
    are resolved (e.g. 'args': [{'stage_id': '${find_stage}'}]).
    """

    def _resolve(value):
        if isinstance(value, str) and value.startswith('${'):
            match = re.match(r'\$\{([^.}]+)(?:\.(\d+))?(?:\.(.+))?\}', value)
            if match:
                step_ref = match.group(1)
                list_index = match.group(2)
                field_ref = match.group(3)
                if step_ref in captured:
                    val = captured[step_ref]
                    # Handle list indexing: ${step.0.field} or ${step.0}
                    if list_index is not None:
                        idx = int(list_index)
                        if isinstance(val, list) and 0 <= idx < len(val):
                            val = val[idx]
                        elif isinstance(val, dict) and idx == 0:
                            # Treat single dict as a 1-element list for ${step.0.field}
                            pass  # val stays as the dict; field_ref extraction follows
                        else:
                            return value  # index out of range or not a list
                    # Handle field access: ${step.field} or ${step.0.field}
                    if field_ref and isinstance(val, dict):
                        result_val = val.get(field_ref)
                        if result_val is None:
                            _logger.warning(
                                "Reference ${%s%s%s} resolved field '%s' to None",
                                step_ref,
                                f'.{list_index}' if list_index else '',
                                f'.{field_ref}',
                                field_ref,
                            )
                        return result_val
                    return val
            return value
        elif isinstance(value, dict):
            return {k: _resolve(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_resolve(v) for v in value]
        return value

    if isinstance(data, dict):
        return {key: _resolve(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [_resolve(v) for v in data]
    return data


def _resolve_references_list(ids, captured):
    """Resolve ${step_id} references in an ids list.

    Handles captured values that are:
      - int → appended directly
      - dict with 'id' key → extracts the id
      - list of ints → extends the list
      - list of dicts → extracts ids from each dict
    """
    if not ids:
        return ids
    resolved = []
    for item in ids:
        if isinstance(item, str) and item.startswith('${'):
            match = re.match(r'\$\{([^}.]+)\}', item)
            if match and match.group(1) in captured:
                val = captured[match.group(1)]
                if isinstance(val, int) and not isinstance(val, bool):
                    resolved.append(val)
                elif isinstance(val, dict) and 'id' in val:
                    resolved.append(val['id'])
                elif isinstance(val, list):
                    if val and isinstance(val[0], dict) and 'id' in val[0]:
                        resolved.extend(v['id'] for v in val)
                    else:
                        resolved.extend(val)
                else:
                    resolved.append(item)
                continue
        resolved.append(item)
    return resolved
