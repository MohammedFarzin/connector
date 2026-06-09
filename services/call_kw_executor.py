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
"""

import json
import logging

from odoo.http import request
from odoo.exceptions import AccessError, ValidationError

from .allowlist import is_allowed
from .serializer import serialize_result

_logger = logging.getLogger(__name__)


def _resolve_xml_ref(env, value):
    """Resolve an XML ID string to its database ID via env.ref().

    Args:
        env: Odoo Environment
        value: str in format 'module.xml_id' (e.g. 'mail.mail_activity_data_meeting')

    Returns:
        int: database ID, or the original value if not an XML ID string
    """
    if isinstance(value, str) and '.' in value and not value.startswith(('http', '$')):
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


def execute_instruction(env, instruction):
    """Execute a single ORM instruction with allowlist check.

    Steps:
      1. Check model.method against allowlist → reject if not allowed
      2. Resolve XML ID references in args/kwargs
      3. Resolve model via Odoo registry
      4. Apply context/sudo as requested
      5. Browse record IDs if provided
      6. Reflect method and execute
      7. Serialize result to JSON-safe format
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
    args = _resolve_xml_refs(env, args)
    kwargs = _resolve_xml_refs(env, kwargs)

    _logger.debug("Executing: %s.%s(sudo=%s)", model_name, method_name, use_sudo)

    try:
        # --- STEP 2: Resolve model ---
        model = env[model_name]

        # --- STEP 3: Apply context and sudo ---
        if context:
            model = model.with_context(**context)
        if use_sudo:
            model = model.sudo()

        # --- STEP 4: Browse records ---
        if record_ids:
            model = model.browse(record_ids)
            if not model.exists():
                return {
                    'success': False,
                    'error': f"No {model_name} records found with IDs {record_ids}",
                }

        # --- STEP 5: Reflect and execute ---
        method = getattr(model, method_name)
        result = method(*args, **kwargs)

        # --- STEP 6: Serialize ---
        return {'success': True, 'result': serialize_result(result)}

    except KeyError:
        return {'success': False, 'error': f"Unknown model: {model_name}"}
    except AttributeError:
        return {'success': False, 'error': f"Model '{model_name}' has no method '{method_name}'"}
    except AccessError as e:
        return {'success': False, 'error': f"Permission denied: {str(e)}"}
    except ValidationError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        _logger.exception("Error executing %s.%s", model_name, method_name)
        return {'success': False, 'error': f"Execution error: {str(e)}"}


def execute_instruction_set(env, instruction_set):
    """Execute a set of instructions, optionally within a transaction."""
    trace_id = instruction_set.get('trace_id', 'unknown')
    transactional = instruction_set.get('transaction', False)
    steps = instruction_set.get('steps', [])

    if not steps:
        return {'success': True, 'trace_id': trace_id, 'results': []}

    captured = {}
    results = []
    error_step = None

    if transactional:
        env.cr.execute('SAVEPOINT crm_assistant_instruction_set')

    try:
        for step in steps:
            step_id = step.get('id', f'step_{len(results)}')
            resolved_kwargs = _resolve_references(step.get('kwargs', {}), captured)
            resolved_ids = _resolve_references_list(step.get('ids'), captured)

            instruction = {
                'model': step.get('model'),
                'method': step.get('method'),
                'ids': resolved_ids,
                'args': step.get('args', []),
                'kwargs': resolved_kwargs,
                'sudo': step.get('sudo', False),
                'context': step.get('context', {}),
            }

            result = execute_instruction(env, instruction)

            if not result['success']:
                error_step = step_id
                results.append({'step': step_id, 'error': result['error']})
                break

            if step.get('capture_result'):
                captured[step_id] = result['result']

            results.append({'step': step_id, 'result': result['result']})

        if error_step and transactional:
            env.cr.execute('ROLLBACK TO SAVEPOINT crm_assistant_instruction_set')
            _logger.warning("Instruction set %s rolled back at %s", trace_id, error_step)
        elif transactional:
            env.cr.execute('RELEASE SAVEPOINT crm_assistant_instruction_set')

        return {'success': error_step is None, 'trace_id': trace_id, 'results': results, 'error_step': error_step}

    except Exception as e:
        if transactional:
            try:
                env.cr.execute('ROLLBACK TO SAVEPOINT crm_assistant_instruction_set')
            except Exception:
                pass
        _logger.exception("Fatal instruction set error: %s", trace_id)
        return {'success': False, 'trace_id': trace_id, 'error': str(e)}


def _resolve_references(kwargs, captured):
    """Resolve ${step_id} and ${step_id.field} references in kwargs."""
    import re
    resolved = {}
    for key, value in kwargs.items():
        if isinstance(value, str) and value.startswith('${'):
            match = re.match(r'\$\{([^}.]+)(?:\.(.+))?\}', value)
            if match:
                step_ref = match.group(1)
                field_ref = match.group(2)
                if step_ref in captured:
                    val = captured[step_ref]
                    if field_ref and isinstance(val, dict):
                        val = val.get(field_ref)
                    resolved[key] = val
                    continue
            resolved[key] = value
        else:
            resolved[key] = value
    return resolved


def _resolve_references_list(ids, captured):
    """Resolve ${step_id} references in an ids list."""
    if not ids:
        return ids
    resolved = []
    for item in ids:
        if isinstance(item, str) and item.startswith('${'):
            import re
            match = re.match(r'\$\{([^}.]+)\}', item)
            if match and match.group(1) in captured:
                val = captured[match.group(1)]
                if isinstance(val, int):
                    resolved.append(val)
                elif isinstance(val, list):
                    resolved.extend(val)
                else:
                    resolved.append(item)
                continue
        resolved.append(item)
    return resolved
