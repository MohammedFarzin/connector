# -*- coding: utf-8 -*-
"""Persistent nonce storage for HMAC replay protection.

Survives connector restarts. Nonces auto-expire after TTL.
"""
from odoo import fields, models, api


class CrmAssistantNonce(models.Model):
    _name = 'crm.assistant.nonce'
    _description = 'CRM Assistant Nonce Store'
    _rec_name = 'nonce'
    _order = 'expires_at asc'

    nonce = fields.Char('Nonce', required=True, index=True)
    expires_at = fields.Datetime('Expires At', required=True, index=True)

    _sql_constraints = [
        ('nonce_unique', 'unique(nonce)', 'Nonce must be unique'),
    ]

    @api.model
    def _gc_nonces(self):
        """Delete expired nonces. Call from cron job or inline."""
        self.search([('expires_at', '<', fields.Datetime.now())]).unlink()
