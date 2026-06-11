# -*- coding: utf-8 -*-
"""
HMAC request signing — signs outgoing requests to the gateway.

The connector signs every POST request so the gateway can verify
the request hasn't been tampered with and originates from a trusted client.

Signing formula:
    message = <json-body>:<nonce>:<timestamp>
    signature = HMAC-SHA256(shared_secret, message)

Headers sent:
    X-Signature  — hex-encoded HMAC
    X-Timestamp  — Unix epoch seconds
    X-Nonce      — UUID4 (replay protection)
    X-Client-Id  — client identifier (from config key)
"""

import hashlib
import hmac
import json
import logging
import time
import uuid

_logger = logging.getLogger(__name__)


def sign_request(secret, body_dict=None, client_id='default', canonical=None):
    """Sign an outgoing request for HMAC-authenticated transmission to the gateway.

    For POST/PUT/PATCH, pass a body_dict and omit canonical (the JSON body
    is signed). For GET/DELETE/HEAD, pass canonical with the URL path +
    sorted query string and omit body_dict.

    Args:
        secret: str — Shared HMAC secret (from ir.config_parameter)
        body_dict: dict or None — The JSON body to sign (POST/PUT/PATCH)
        client_id: str — Client identifier (gateway uses this to look up per-client secrets)
        canonical: str or None — Override canonical string for GET/DELETE/HEAD
            (e.g. '/api/v1/verify'). When set, body_dict is ignored.

    Returns:
        dict: Headers to include in the httpx request
              {'X-Signature': ..., 'X-Timestamp': ..., 'X-Nonce': ..., 'X-Client-Id': ...}
    """
    if canonical is not None and body_dict is not None:
        raise ValueError("Provide body_dict or canonical, not both")
    if canonical is None and body_dict is None:
        raise ValueError("Must provide body_dict (for POST/PUT/PATCH) or canonical (for GET/DELETE/HEAD)")

    nonce = str(uuid.uuid4())
    timestamp = str(int(time.time()))

    if canonical is not None:
        message = f"{canonical}:{nonce}:{timestamp}"
    else:
        body_str = json.dumps(body_dict, sort_keys=True)
        message = f"{body_str}:{nonce}:{timestamp}"

    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    _logger.debug("Signed request (client=%s, nonce=%s)", client_id, nonce[:8])

    return {
        'X-Signature': signature,
        'X-Timestamp': timestamp,
        'X-Nonce': nonce,
        'X-Client-Id': client_id,
    }
