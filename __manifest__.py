# -*- coding: utf-8 -*-
# Copyright (C) 2026 — MIT License
{
    'name': 'CRM Assistant Connector',
    'version': '1.0.0',
    'summary': 'Thin connector for CRM Assistant SaaS — generic ORM executor + SSE relay',
    'description': """
Lightweight Odoo module bridging CRM Assistant SaaS platform to local Odoo.
Contains NO business logic, tool definitions, AI prompts, or agent code.
Open-sourced under MIT — safe to distribute freely.
    """,
    'category': 'Sales/CRM',
    'author': 'CRM Assistant',
    'depends': ['crm', 'calendar', 'web', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': ['crm_assistant_connector/static/src/**/*'],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'Other OSI approved licence',

}
