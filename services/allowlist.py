# -*- coding: utf-8 -*-
"""
Hardcoded model.method allowlist for call_kw executor.

Defense-in-depth: even with a valid HMAC signature, only explicitly
permitted model.method combinations are allowed. This prevents an
attacker who compromises the HMAC secret from gaining full ORM access.

To add a new tool: add its required model.method entries here AND
the corresponding tool mapping in the gateway's instruction_builder.
"""

# Format: model_name → set of allowed method names
ALLOWLIST = {
    'crm.lead': {
        'create', 'write', 'search', 'search_read', 'search_count', 'read', 'browse',
        'action_set_won', 'action_set_lost', 'message_post',
        'activity_schedule',
    },
    'crm.stage': {
        'search', 'search_read', 'search_count', 'read', 'browse',
    },
    'calendar.event': {
        'create', 'write', 'search', 'search_read', 'search_count', 'read', 'browse',
        'action_cancel', 'unlink',
    },
    'calendar.attendee': {
        'search', 'read', 'browse',
    },
    'mail.activity': {
        'search', 'read', 'browse', 'create',
    },
    'mail.activity.type': {
        'search', 'read', 'browse',
    },
    'hr.employee': {
        'search', 'search_read', 'read', 'browse',
    },
    'hr.department': {
        'search', 'read', 'browse',
    },
    'res.users': {
        'search', 'search_read', 'search_count', 'read', 'browse',
    },
    'res.partner': {
        'search', 'search_read', 'search_count', 'read', 'browse',
    },
    'ir.model': {
        'search', 'read', 'browse',
    },
    'bus.bus': {
        '_sendone',
    },
}


def is_allowed(model_name, method_name):
    """Check if a model.method combination is in the allowlist.

    Args:
        model_name: str — Odoo model technical name
        method_name: str — method name to call

    Returns:
        bool — True if allowed, False if blocked
    """
    allowed_methods = ALLOWLIST.get(model_name)
    if allowed_methods is None:
        return False
    return method_name in allowed_methods
