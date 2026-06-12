# -*- coding: utf-8 -*-
import logging
import os
from logging.handlers import RotatingFileHandler

from . import models
from . import controllers

# ---------------------------------------------------------------------------
# Dedicated connector log file (in addition to Odoo's --logfile)
# ---------------------------------------------------------------------------
# Writes to the Odoo data directory by default.  Set the environment
# variable CONNECTOR_LOG_FILE to override (e.g., /var/log/crm-connector.log).
_connector_logger = logging.getLogger('odoo.addons.crm_assistant_connector')
_default_log_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'logs', 'connector.log'
)
_log_path = os.environ.get('CONNECTOR_LOG_FILE', _default_log_path)
try:
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
    _fh = RotatingFileHandler(
        _log_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    _connector_logger.addHandler(_fh)
    _connector_logger.info("Connector log file: %s", _log_path)
except OSError as e:
    print(f"[connector] WARNING: Could not open log file {_log_path}: {e}")
