# -*- coding: utf-8 -*-
"""
HMAC signature verification with persistent nonce storage.

Key fix: Uses threading.RLock (reentrant) to prevent deadlock.
Nonces are persisted in crm.assistant.nonce model for cross-restart protection.

Accepts an optional `env` parameter to avoid dependency on odoo.http.request,
enabling use in background threads, cron jobs, and unit tests.
"""

import hashlib
import hmac
import logging
import time
from threading import RLock

_logger = logging.getLogger(__name__)

MAX_PAYLOAD_AGE_SECONDS = 300  # 5 minutes
CLOCK_SKEW_TOLERANCE = 30      # Accept ±30s clock skew for timestamps
_NONCE_LOCK = RLock()


def _get_shared_secret(env=None):
    """Get the shared HMAC secret from config.

    Args:
        env: Odoo Environment (optional). If not provided, falls back to
             odoo.http.request.env for backward compatibility.

    Returns:
        str or None: The shared secret, or None if not configured.
    """
    if env is not None:
        return env['ir.config_parameter'].sudo().get_param(
            'crm_assistant_connector.secret', ''
        ) or None
    # Fallback: HTTP request context
    try:
        from odoo.http import request
        if request:
            return request.env['ir.config_parameter'].sudo().get_param(
                'crm_assistant_connector.secret', ''
            ) or None
    except RuntimeError as e:
        if 'not bound' not in str(e):
            raise
    return None


def _check_and_record_nonce(env, nonce, now):
    """Check nonce uniqueness using persistent DB storage."""
    Nonce = env['crm.assistant.nonce'].sudo()
    # First garbage-collect expired nonces
    Nonce._gc_nonces()
    # Check if this nonce exists
    existing = Nonce.search([('nonce', '=', nonce)], limit=1)
    if existing:
        return False
    # Record this nonce with TTL
    from datetime import datetime, timedelta
    expires = datetime.now() + timedelta(seconds=MAX_PAYLOAD_AGE_SECONDS)
    Nonce.create({'nonce': nonce, 'expires_at': expires})
    return True


def verify_signature(payload, signature, nonce, timestamp, env=None):
    """Verify HMAC-SHA256 signature on a payload.

    Args:
        payload: str — the signed payload (JSON string)
        signature: str — hex-encoded HMAC-SHA256 signature
        nonce: str — unique nonce for replay protection
        timestamp: str — ISO 8601 timestamp
        env: Odoo Environment (optional). Required for nonce persistence.
             If not provided in non-HTTP contexts, nonce checking is skipped.

    Returns: {'valid': True} or {'valid': False, 'error': 'reason'}
    """
    secret = _get_shared_secret(env)
    if not secret:
        return {'valid': False, 'error': 'Connector not configured. Set shared secret in Settings.'}

    # Verify timestamp freshness (future timestamps beyond skew are rejected)
    try:
        from datetime import datetime, timezone
        payload_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = (now - payload_time).total_seconds()
        if delta > MAX_PAYLOAD_AGE_SECONDS + CLOCK_SKEW_TOLERANCE:
            return {'valid': False, 'error': f'Payload expired (age: {delta:.0f}s).'}
        if delta < -CLOCK_SKEW_TOLERANCE:
            return {'valid': False, 'error': 'Payload timestamp is in the future.'}
    except (ValueError, TypeError):
        return {'valid': False, 'error': 'Invalid timestamp format.'}

    # Verify nonce uniqueness (persistent) — requires env
    if env is not None:
        with _NONCE_LOCK:
            now_epoch = time.time()
            if not _check_and_record_nonce(env, nonce, now_epoch):
                return {'valid': False, 'error': 'Duplicate nonce — possible replay attack.'}
    else:
        _logger.warning("No env provided — nonce replay protection skipped")

    # Verify HMAC signature
    message = f"{payload}:{nonce}:{timestamp}"
    expected = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        _logger.warning("HMAC verification failed for nonce=%s", nonce)
        return {'valid': False, 'error': 'Invalid signature.'}

    return {'valid': True}
