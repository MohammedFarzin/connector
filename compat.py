# -*- coding: utf-8 -*-
"""
Cross-version compatibility shims for Odoo 16.0 ↔ 18.0.

All helpers use feature-detection (not version-branching) so callers
never need to write ``if version == 16`` themselves.
"""

import html

from odoo import release


# ── version introspection ───────────────────────────────────────────────────

def odoo_version():
    """Return ``(major, minor)`` tuple e.g. ``(18, 0)``."""
    return (release.version_info[0], release.version_info[1])


def is_v16():
    """True when running on Odoo 16.0 Community."""
    return release.version_info[0] == 16


def is_v17_plus():
    """True when running on Odoo 17.0 or later."""
    return release.version_info[0] >= 17


def is_v18_plus():
    """True when running on Odoo 18.0 or later."""
    return release.version_info[0] >= 18


# ── bus dispatch ────────────────────────────────────────────────────────────

def bus_send(env, channel, notification_type, message):
    """Dispatch a bus notification — works on v16 and v18.

    On both versions ``bus.bus`` exposes ``_sendone(channel, type, msg)``.
    This helper provides a single call-site so future version quirks can be
    centralised here.
    """
    env['bus.bus'].sudo()._sendone(channel, notification_type, message)


# ── safe markup ─────────────────────────────────────────────────────────────

def safe_markup(text):
    """Return ``Markup(text)`` on v17+ else ``str(text)`` on v16.

    On v16, ``Markup`` is not available as a standalone type; the caller is
    responsible for escaping user-controlled input **before** calling this
    function.  The return value is safe to assign to a template variable.
    """
    try:
        from markupsafe import Markup  # v17+ / v18
        return Markup(text)
    except ImportError:
        from odoo.tools import markupsafe  # v16 fallback
        try:
            return markupsafe.Markup(text)
        except (AttributeError, ImportError):
            return text  # plain str — caller must ensure safety


# ── HTML escape ─────────────────────────────────────────────────────────────

def escape_html(text):
    """Escape HTML entities in *text*.  Works on all versions."""
    return html.escape(text)


# ── safe command namespace ──────────────────────────────────────────────────

def safe_command_namespace():
    """Return ``odoo.fields.Command`` — available on both v16 and v18.

    The Command enum exposes ``.create``, ``.update``, ``.unlink``,
    ``.delete``, ``.clear``, ``.set``, ``.link`` as static methods that
    return legacy tuples ``(N, args...)`` on both versions.

    NOTE: The method is called ``.update``, NOT ``.write``.
    """
    from odoo.fields import Command
    return Command
