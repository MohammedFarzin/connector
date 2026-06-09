# -*- coding: utf-8 -*-
"""
Recursive Odoo type → JSON-safe serializer.

Handles all Odoo result types that the call_kw executor may return:
- Recordsets → [{id, fields...}] for multi, {id, fields...} for single, [] for empty
- Datetime objects → ISO 8601 strings
- Binary fields → null (never leak binary data)
- Many2one references → (id, display_name) tuples
- x2many commands → preserved as Odoo wire format
- Primitive types → passed through
"""

import json as _json
from datetime import datetime, date

from odoo import models


def serialize_result(result):
    """Convert any Odoo return value to a JSON-safe structure.

    Args:
        result: Any return from an Odoo model method

    Returns:
        JSON-serializable value
    """
    # Recordset (multi-record)
    if isinstance(result, models.BaseModel):
        if not result:
            return []
        if len(result) == 1:
            return _serialize_single_record(result)
        return [_serialize_single_record(r) for r in result]

    # Datetime
    if isinstance(result, datetime):
        return result.isoformat()

    # Date
    if isinstance(result, date):
        return result.isoformat()

    # List of recordsets (from search_read, etc.)
    if isinstance(result, list):
        return [_serialize_value(item) for item in result]

    # Dict (from read, etc.)
    if isinstance(result, dict):
        return {k: _serialize_value(v) for k, v in result.items()}

    # Pass through: int, str, float, bool, None
    return result


def _serialize_single_record(record):
    """Serialize a single Odoo record as a dict of {field: value}."""
    # Use read() to get all field values properly
    data = record.read()[0] if record else {}
    # Strip binary fields
    for field_name, field_obj in record._fields.items():
        if field_obj.type == 'binary':
            data[field_name] = None
    # Convert datetime objects in the dict
    return {k: _serialize_value(v) for k, v in data.items()}


def _serialize_value(value):
    """Recursively serialize a single value."""
    if isinstance(value, models.BaseModel):
        return _serialize_single_record(value) if value else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    return value
