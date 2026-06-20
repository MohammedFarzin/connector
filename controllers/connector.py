# -*- coding: utf-8 -*-
"""
CRM Assistant Connector Controller — fixed implementation.

Endpoints:
  POST /crm_assistant_connector/message    — Widget → Gateway → Execute plans
  POST /crm_assistant_connector/execute    — Gateway-only (HMAC auth, no uid check)
  GET  /crm_assistant_connector/stream     — Bus-based SSE relay
  POST /crm_assistant_connector/handshake  — Reports connector version
  POST /crm_assistant_connector/reset      — Clear session
  POST /crm_assistant_connector/restore    — Load session history
  POST /crm_assistant_connector/sessions   — List user sessions
  GET  /crm_assistant_connector/ping       — Health check
"""

import json
import logging
import time
import httpx

from odoo import http
from odoo.http import request

from ..services.signature import verify_signature
from ..services.signing import sign_request
from ..services.call_kw_executor import execute_instruction_set
from ..services.stream_relay import start_relay

_logger = logging.getLogger(__name__)

# Session store (in-memory — production would use Odoo model)
_SESSION_STORE = {}
_MAX_HISTORY = 50

# Per-user reload flag — stored in ir_config_parameter so it works
# across multiple worker processes (not just workers=0).
_RELOAD_PARAM_PREFIX = 'crm_assistant.needs_reload.'


def _set_reload_flag(env, user_id):
    """Flag a user for frontend reload after a write operation."""
    env['ir.config_parameter'].sudo().set_param(
        f'{_RELOAD_PARAM_PREFIX}{user_id}', '1'
    )


def _check_reload_flag(env, uid):
    """Check and clear the reload flag for a user. Returns bool."""
    # Use raw SQL to bypass ORM cache — get_param's @ormcache serves
    # stale results across request boundaries.
    env.cr.execute(
        "SELECT value FROM ir_config_parameter WHERE key = %s",
        (f'{_RELOAD_PARAM_PREFIX}{uid}',)
    )
    row = env.cr.fetchone()
    if row and row[0] == '1':
        env.cr.execute(
            "UPDATE ir_config_parameter SET value = '0' WHERE key = %s",
            (f'{_RELOAD_PARAM_PREFIX}{uid}',)
        )
        return True
    return False


def _get_config(key, default=''):
    return request.env['ir.config_parameter'].sudo().get_param(
        f'crm_assistant_connector.{key}', default
    )


def _get_secret():
    return _get_config('secret', '')


def _get_gateway_url():
    return _get_config('gateway_url', '')


def _get_client_id():
    """Return a stable client identifier for HMAC signing.

    Uses the first 8 chars of the Odoo database UUID as a stable,
    unique-per-installation client ID. Falls back to 'default' if
    the database UUID can't be read.
    """
    try:
        db_uuid = request.env['ir.config_parameter'].sudo().get_param('database.uuid', '')
        if db_uuid:
            return db_uuid[:8]
    except Exception as e:
        _logger.debug("Could not read database UUID for client_id: %s", e)
    return 'default'


# =========================================================================
# Controller
# =========================================================================

class CrmAssistantConnectorController(http.Controller):

    # ============================
    # /message — Widget entry point
    # ============================

    @http.route('/crm_assistant_connector/message', type='json', auth='public')
    def process_message(self, message, session_id=None, history=None, **kwargs):
        """Forward user message to gateway, execute returned plans, return response.

        FIXED: Iterates over execution plans list, verifies HMAC PER PLAN,
        executes each plan individually.
        """
        if not request.env.uid:
            return {'error': 'Please log in to use the CRM Assistant.'}
        if not message or not message.strip():
            return {'error': 'Empty message.'}

        gateway_url = _get_gateway_url()
        secret = _get_secret()
        if not gateway_url or not secret:
            return {'error': 'CRM Assistant not configured. Check Settings.'}

        # Build session history
        session_id, session_history = _get_session(session_id, request.env.uid)

        # Forward to gateway (HMAC-signed + Bearer token)
        client_id = _get_client_id()
        payload = {
            'message': message,
            'session_id': session_id,
            'user_id': request.env.uid,
        }
        # Serialize payload to JSON FIRST, then sign the exact bytes
        # that will go over the wire. This guarantees the HMAC canonical
        # matches the raw body the gateway reads, regardless of httpx's
        # internal JSON serializer.
        import json as _json
        body_str = _json.dumps(payload, sort_keys=True)
        hmac_headers = sign_request(secret, canonical=body_str, client_id=client_id)

        try:
            import base64
            safe_secret = base64.b64encode(secret.encode()).decode()
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{gateway_url}/api/v1/message",
                    headers={
                        'Authorization': f'Bearer {safe_secret}',
                        'Content-Type': 'application/json',
                        **hmac_headers,
                    },
                    content=body_str,
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', 'unknown') if isinstance(e, httpx.HTTPError) else 'parse_error'
            _logger.error("Gateway error (HTTP %s): %s", status_code, e)
            return {'error': 'AI service temporarily unavailable. Please try again.'}

        # Process execution plans (FIXED: iterate list, verify HMAC per plan)
        execution_plans = data.get('execution_plans', [])
        execution_results = []

        for plan_entry in execution_plans:
            tool_name = plan_entry.get('tool_name')
            signed = plan_entry.get('plan', {})

            if plan_entry.get('error'):
                execution_results.append({'tool': tool_name, 'error': plan_entry['error']})
                continue

            # FIXED: Verify HMAC before execution
            verification = verify_signature(
                signed.get('payload', ''),
                signed.get('signature', ''),
                signed.get('nonce', ''),
                signed.get('timestamp', ''),
                env=request.env,
            )
            if not verification['valid']:
                _logger.warning("HMAC rejected for tool %s: %s", tool_name, verification.get('error'))
                execution_results.append({'tool': tool_name, 'error': 'Signature verification failed'})
                continue

            # Parse the instruction set from the signed payload
            try:
                instruction_set = json.loads(signed['payload'])
            except json.JSONDecodeError:
                execution_results.append({'tool': tool_name, 'error': 'Invalid instruction payload'})
                continue

            # Execute
            result = execute_instruction_set(request.env, instruction_set)
            execution_results.append({'tool': tool_name, 'result': result})

        # Store in session
        session_history.append({'role': 'user', 'content': message})
        session_history.append({
            'role': 'assistant',
            'content': data.get('text', ''),
            'html': data.get('html', ''),
        })
        _save_session(session_id, session_history)

        # Check if /execute flagged any writes during this exchange.
        uid = request.env.uid
        needs_reload = _check_reload_flag(request.env, uid)
        if needs_reload:
            _logger.info("Reload flag found for user %s — telling frontend to refresh", uid)

        return {
            'text': data.get('text', ''),
            'html': data.get('html', ''),
            'session_id': session_id,
            'execution_results': execution_results,
            'reload': needs_reload,
        }

    # ============================
    # /execute — Gateway calls this
    # ============================

    @http.route('/crm_assistant_connector/execute', type='json', auth='public')
    def execute(self, payload, signature, nonce, timestamp, **kwargs):
        """Execute a signed instruction set from the gateway.

        FIXED: No uid check — HMAC verification is the sole auth mechanism.
        UID is extracted from the signed payload (set by gateway from user context).
        """
        # FIXED: HMAC verification BEFORE any uid/access check
        verification = verify_signature(payload, signature, nonce, timestamp, env=request.env)
        if not verification['valid']:
            return {'success': False, 'error': verification.get('error'), 'blocked': True}

        try:
            instruction_set = json.loads(payload)
        except json.JSONDecodeError:
            return {'success': False, 'error': 'Invalid JSON payload'}

        trace_id = instruction_set.get('trace_id', 'unknown')
        user_id = instruction_set.get('user_id', request.env.uid or 1)

        # Execute as the target user
        env = request.env(user=user_id)
        result = execute_instruction_set(env, instruction_set)

        # Flag user for frontend reload if any writes were dispatched.
        # Uses result['had_writes'] from execute_instruction_set — it
        # tracks actual notification dispatches, not method-name guesses.
        if result.get('had_writes'):
            _set_reload_flag(request.env, user_id)
            _logger.info(
                "Reload flag SET for user %s (instruction set trace=%s)",
                user_id, trace_id
            )

        return result

    # ============================
    # /stream — Bus-based SSE relay
    # ============================

    @http.route('/crm_assistant_connector/stream', type='http', auth='public')
    def stream(self, **kwargs):
        """Initialize the bus-based SSE relay.

        FIXED: Now starts a single background relay thread (not per-request
        blocking proxy). Widget subscribes via bus.bus longpoll.
        """
        gateway_url = _get_gateway_url()
        secret = _get_secret()
        if gateway_url and secret:
            start_relay(gateway_url, secret)
        return request.make_response(
            'data: {"status": "relay_started"}\n\n',
            headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'},
        )

    # ============================
    # /handshake — Version exchange
    # ============================

    @http.route('/crm_assistant_connector/handshake', type='json', auth='public')
    def handshake(self, **kwargs):
        """Report connector version and Odoo version to gateway."""
        if not request.env.uid:
            return {'error': 'Authentication required.'}
        return {
            'connector_version': '1.0.0',
            'odoo_version': request.env['ir.module.module'].sudo().search(
                [('name', '=', 'base')], limit=1
            ).latest_version or 'unknown',
        }

    # ============================
    # /reset — Clear session
    # ============================

    @http.route('/crm_assistant_connector/reset', type='json', auth='public')
    def reset_session(self, session_id, **kwargs):
        """Clear a session's conversation history."""
        if not request.env.uid:
            return {'error': 'Please log in.'}
        if session_id in _SESSION_STORE:
            del _SESSION_STORE[session_id]
        return {'status': 'ok', 'session_id': str(time.time())}

    # ============================
    # /restore — Load session
    # ============================

    @http.route('/crm_assistant_connector/restore', type='json', auth='public')
    def restore_session(self, session_id, **kwargs):
        """Restore conversation history for a session."""
        if not request.env.uid:
            return {'session_id': None, 'messages': [], 'found': False}
        if session_id in _SESSION_STORE:
            return {
                'session_id': session_id,
                'messages': _SESSION_STORE[session_id],
                'found': True,
            }
        return {'session_id': None, 'messages': [], 'found': False}

    # ============================
    # /sessions — List sessions
    # ============================

    @http.route('/crm_assistant_connector/sessions', type='json', auth='public')
    def list_sessions(self, **kwargs):
        """List all sessions for the current user."""
        if not request.env.uid:
            return {'error': 'Please log in.'}
        return {
            'sessions': [
                {'session_id': k, 'message_count': len(v)}
                for k, v in _SESSION_STORE.items()
            ]
        }

    # ============================
    # /ping — Health check
    # ============================

    @http.route('/crm_assistant_connector/ping', type='json', auth='public')
    def ping(self, **kwargs):
        """Health check — used by Settings → Test Connection."""
        secret = _get_secret()
        gateway_url = _get_gateway_url()
        if not secret or not gateway_url:
            return {'status': 'not_configured'}

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(f"{gateway_url}/api/v1/health")
                resp.raise_for_status()
                info = resp.json()
            return {
                'status': 'connected',
                'gateway_version': info.get('version', 'unknown'),
                'uptime': info.get('uptime', 0),
            }
        except Exception:
            return {'status': 'gateway_unreachable'}


# =========================================================================
# Session helpers
# =========================================================================

def _get_session(session_id, user_id):
    if session_id and session_id in _SESSION_STORE:
        return session_id, _SESSION_STORE[session_id]
    new_id = session_id or str(time.time())
    _SESSION_STORE[new_id] = []
    return new_id, []


def _save_session(session_id, history):
    if len(history) > _MAX_HISTORY:
        history = history[-_MAX_HISTORY:]
    _SESSION_STORE[session_id] = history
