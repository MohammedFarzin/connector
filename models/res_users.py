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
        """Called by the Test Connection button in Settings."""
        # Import inline to avoid circular dependency at module load
        import httpx, json
        gateway_url = self.env['ir.config_parameter'].sudo().get_param(
            'crm_assistant_connector.gateway_url', ''
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
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(f"{gateway_url}/api/v1/health")
                resp.raise_for_status()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Connection Test',
                        'message': f"Connected to CRM Assistant Gateway v{resp.json().get('version','?')}",
                        'type': 'success',
                    },
                }
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Connection Failed',
                    'message': str(e),
                    'type': 'danger',
                },
            }
