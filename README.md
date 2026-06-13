# CRM Assistant Connector

Thin Odoo module that connects your on-premise Odoo instance to the CRM Assistant SaaS gateway.

## Installation

Clone into your Odoo addons directory:

```bash
cd /path/to/odoo/addons
git clone https://github.com/MohammedFarzin/connector.git crm_assistant_connector
```

Or install as a submodule:

```bash
git submodule add https://github.com/MohammedFarzin/connector.git custom-addons/crm_assistant_connector
```

Then install the module from the Odoo Apps menu.

## Requirements

- Odoo 18.0+
- `crm_assistant` base module (for shared JS components)

## Configuration

After installation, go to Settings → CRM Assistant to configure:
- API Gateway URL
- API Key
- HMAC Secret (for request signing)

## License

MIT
