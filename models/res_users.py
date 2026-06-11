# -*- coding: utf-8 -*-
"""User model extensions — stores config in ir.config_parameter, NOT on res.users."""

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    crm_assistant_gateway_url = fields.Char(
        'Gateway URL', config_parameter='crm_assistant_connector.gateway_url',
        default='',
    )
    crm_assistant_secret = fields.Char(
        'Shared Secret', config_parameter='crm_assistant_connector.secret',
    )

    def test_crm_assistant_connection(self):
        """Called by the Test Connection button in Settings.

        Sends an HMAC-signed GET to /api/v1/verify — this proves:
        1. The gateway is reachable (network connectivity)
        2. The shared secret matches the gateway (HMAC verification)
        3. The client is registered in CLIENT_REGISTRY (Bearer auth)
        """
        import base64, httpx, json, logging
        _logger = logging.getLogger(__name__)
        # Import at module level, not inside try/catch, so ImportError
        # from a broken deployment isn't masked as "Connection Failed".
        from ..services.signing import sign_request

        gateway_url = self.env['ir.config_parameter'].sudo().get_param(
            'crm_assistant_connector.gateway_url', ''
        ).rstrip('/')
        secret = self.env['ir.config_parameter'].sudo().get_param(
            'crm_assistant_connector.secret', ''
        )
        if not gateway_url:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Test',
                    'message': 'Gateway URL is not configured.',
                    'type': 'warning',
                },
            }
        if gateway_url.startswith('http://'):
            _logger.warning("Gateway URL uses plain HTTP — secret will be transmitted in cleartext")
        if not secret or not secret.strip():
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Test',
                    'message': 'Shared Secret is not configured.',
                    'type': 'warning',
                },
            }
        try:
            # Use the same client_id derivation as the controller (first 8
            # chars of database UUID) so the gateway can match per-client
            # HMAC secrets when configured.
            db_uuid = self.env['ir.config_parameter'].sudo().get_param(
                'database.uuid', ''
            )
            client_id = db_uuid[:8] if db_uuid else 'default'
            # Build HMAC-signed GET request — sign the URL path (gateway signs
            # path + sorted query for GET requests, not a JSON body)
            headers = sign_request(
                secret, canonical='/api/v1/verify', client_id=client_id
            )
            # Base64-encode the secret in the Authorization header to prevent
            # header injection if the secret contains CRLF or control chars.
            safe_secret = base64.b64encode(secret.strip().encode()).decode()
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{gateway_url}/api/v1/verify",
                    headers={
                        'Authorization': f'Bearer {safe_secret}',
                        **headers,
                    },
                )
                if resp.status_code == 401:
                    # Include the gateway's detail message so users can
                    # distinguish "clock is wrong" from "secret is wrong".
                    detail = ''
                    try:
                        detail = resp.json().get('detail', '')
                    except json.JSONDecodeError:
                        pass
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Authentication Failed',
                            'message': (
                                'The gateway rejected your credentials'
                                + (f': {detail}' if detail else '.')
                            ),
                            'type': 'danger',
                        },
                    }
                resp.raise_for_status()
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': 'Connection Failed',
                            'message': 'Gateway returned a non-JSON response.',
                            'type': 'danger',
                        },
                    }
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Connection Verified',
                        'message': (
                            f"Authenticated as '{data.get('client_name','?')}' "
                            f"(plan: {data.get('plan','?')}, "
                            f"gateway v{data.get('version','?')})"
                        ),
                        'type': 'success',
                    },
                }
        except httpx.HTTPError as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Failed',
                    'message': f'Gateway HTTP error: {e}',
                    'type': 'danger',
                },
            }
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Failed',
                    'message': f'{type(e).__name__}: {e}',
                    'type': 'danger',
                },
            }
