# -*- coding: utf-8 -*-
"""Company-level graceful degradation — auto-disable when gateway unreachable."""

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    crm_assistant_disabled = fields.Boolean(
        'CRM Assistant Disabled',
        default=False,
        help='Automatically set when the gateway is unreachable for >2 minutes. '
             'The chat widget hides when this is True. Admins can manually reset.',
    )
