# -*- coding: utf-8 -*-
"""
Bus-based SSE relay — single persistent connection per Odoo instance.

Maintains ONE background SSE connection to the gateway (not per-user).
Tokens are dispatched via Odoo's native bus.bus to per-session channels.
This avoids the worker-blocking anti-pattern identified in the pre-mortem.
"""

import json
import logging
import threading
import time

_logger = logging.getLogger(__name__)

# Singleton relay state
_relay_thread = None
_relay_running = False
_relay_lock = threading.Lock()


def start_relay(gateway_url, secret):
    """Start the background SSE relay if not already running."""
    global _relay_thread, _relay_running
    with _relay_lock:
        if _relay_running:
            return
        _relay_running = True
        _relay_thread = threading.Thread(
            target=_relay_loop,
            args=(gateway_url, secret),
            daemon=True,
        )
        _relay_thread.start()
        _logger.info("SSE relay started: %s", gateway_url)


def _relay_loop(gateway_url, secret):
    """Background loop: connect to gateway SSE, fan out to bus.bus."""
    import httpx
    from odoo import api, registry

    consecutive_errors = 0

    while _relay_running:
        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream(
                    'GET',
                    f"{gateway_url}/api/v1/stream",
                    headers={'Authorization': f'Bearer {secret}'},
                ) as stream:
                    stream.raise_for_status()
                    consecutive_errors = 0

                    for line in stream.iter_lines():
                        if not line or not _relay_running:
                            continue
                        if line.startswith('data: '):
                            try:
                                data = json.loads(line[6:])
                                session_id = data.get('session_id', '')
                                channel = f'crm_assistant_stream_{session_id}'
                                # Fan out to bus — this reaches all widget subscribers
                                _dispatch_to_bus(channel, data)
                            except json.JSONDecodeError:
                                pass

        except Exception as e:
            _logger.warning("SSE relay error (%d): %s", consecutive_errors, e)
            consecutive_errors += 1
            if consecutive_errors > 10:
                _logger.error("SSE relay: too many errors, stopping")
                break
            time.sleep(min(2 ** consecutive_errors, 30))


def _dispatch_to_bus(channel, data):
    """Dispatch a token/event to an Odoo bus channel."""
    try:
        # Get a fresh cursor and env
        from odoo import api, sql_db
        db_name = None
        # Try to get the current registry's db name
        # This runs in a background thread, so we need to manage our own cursor
        import odoo
        db_name = odoo.tools.config['db_name']
        if not db_name:
            return
        registry_obj = odoo.registry(db_name)
        with registry_obj.cursor() as cr:
            env = api.Environment(cr, 1, {})  # uid=1 (admin) for bus access
            env['bus.bus'].sudo()._sendone(channel, 'crm_assistant_token', data)
            cr.commit()
    except Exception as e:
        _logger.debug("Bus dispatch skipped: %s", e)
