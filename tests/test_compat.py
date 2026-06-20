# -*- coding: utf-8 -*-
"""
Tests for cross-version compatibility shims (compat.py).

Each test exercises a compat helper to guarantee it works on both
Odoo 16 Community and Odoo 18 Community.
"""

from odoo.tests import common

from .. import compat


class TestCompatVersionDetection(common.TransactionCase):
    """Test that version introspection helpers return expected values."""

    def test_odoo_version_is_tuple(self):
        version = compat.odoo_version()
        self.assertIsInstance(version, tuple)
        self.assertEqual(len(version), 2)
        self.assertIsInstance(version[0], int)
        self.assertIsInstance(version[1], int)

    def test_is_v16_or_v18_mutually_exclusive(self):
        """Exactly one of is_v16 / is_v18_plus must be True."""
        v16 = compat.is_v16()
        v18p = compat.is_v18_plus()
        # They can't both be True simultaneously
        self.assertFalse(v16 and v18p, "Both is_v16 and is_v18_plus returned True")
        # At least one is True (we're running on SOME Odoo version)
        self.assertTrue(v16 or v18p, "Neither is_v16 nor is_v18_plus returned True")

    def test_is_v17_plus_consistent(self):
        """is_v17_plus must be consistent with is_v16 / is_v18_plus."""
        if compat.is_v16():
            self.assertFalse(compat.is_v17_plus())
        if compat.is_v18_plus():
            self.assertTrue(compat.is_v17_plus())


class TestCompatBusSend(common.TransactionCase):
    """Test bus_send helper resolves the method name correctly."""

    def test_bus_send_is_callable(self):
        self.assertTrue(callable(compat.bus_send), "bus_send must be a callable")

    def test_bus_send_accepts_four_args(self):
        """bus_send(env, channel, notification_type, message) signature."""
        import inspect
        sig = inspect.signature(compat.bus_send)
        params = list(sig.parameters.keys())
        self.assertGreaterEqual(len(params), 4, f"Expected ≥4 params, got {params}")

    def test_bus_send_no_error_with_env(self):
        """Calling bus_send with a valid env should not raise AttributeError.

        The actual bus dispatch may fail (no bus module installed, etc.)
        but the method-name resolution should succeed.
        """
        try:
            compat.bus_send(self.env, 'test_channel', 'test_type', {'msg': 'hello'})
        except AttributeError as e:
            self.fail(f"bus_send raised AttributeError (method name not found): {e}")
        except Exception:
            # Other exceptions (e.g., no bus module, missing channel, etc.)
            # are acceptable — we're only testing method resolution.
            pass


class TestCompatSafeMarkup(common.TransactionCase):
    """Test safe_markup returns appropriate types on each version."""

    def test_safe_markup_returns_string_or_markup(self):
        result = compat.safe_markup("<b>test</b>")
        self.assertIsInstance(result, str, "safe_markup must return a string")

    def test_safe_markup_preserves_html(self):
        result = compat.safe_markup("<b>bold</b>")
        self.assertIn("<b>bold</b>", str(result),
                       "safe_markup must preserve HTML content")

    def test_safe_markup_handles_empty_string(self):
        result = compat.safe_markup("")
        self.assertEqual(str(result), "")

    def test_safe_markup_handles_plain_text(self):
        result = compat.safe_markup("Hello, World!")
        self.assertIn("Hello, World!", str(result))


class TestCompatEscapeHtml(common.TransactionCase):
    """Test escape_html helper."""

    def test_escape_html_escapes_brackets(self):
        result = compat.escape_html("<script>alert('xss')</script>")
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_escape_html_preserves_plain_text(self):
        result = compat.escape_html("Hello, World!")
        self.assertEqual(result, "Hello, World!")


class TestCompatSafeCommandNamespace(common.TransactionCase):
    """Test safe_command_namespace exposes the expected Command-like API."""

    def test_command_namespace_has_create(self):
        ns = compat.safe_command_namespace()
        self.assertTrue(hasattr(ns, 'create'), "Missing .create")

    def test_command_namespace_has_update(self):
        ns = compat.safe_command_namespace()
        self.assertTrue(hasattr(ns, 'update'), "Missing .update")

    def test_command_namespace_has_unlink(self):
        ns = compat.safe_command_namespace()
        self.assertTrue(hasattr(ns, 'unlink'), "Missing .unlink")

    def test_command_namespace_has_clear(self):
        ns = compat.safe_command_namespace()
        self.assertTrue(hasattr(ns, 'clear'), "Missing .clear")

    def test_command_namespace_has_set(self):
        ns = compat.safe_command_namespace()
        self.assertTrue(hasattr(ns, 'set'), "Missing .set")

    def test_command_namespace_has_link(self):
        ns = compat.safe_command_namespace()
        self.assertTrue(hasattr(ns, 'link'), "Missing .link")

    def test_command_create_returns_tuple(self):
        """Command.create(vals) returns (0, 0, vals) tuple on both v16 and v18."""
        ns = compat.safe_command_namespace()
        result = ns.create({'name': 'Test'})
        self.assertIsInstance(result, tuple,
                              "Command.create must return tuple")
        self.assertEqual(len(result), 3)

    def test_command_clear_returns_tuple(self):
        ns = compat.safe_command_namespace()
        result = ns.clear()
        self.assertIsInstance(result, tuple,
                              "Command.clear must return tuple")
        self.assertEqual(len(result), 3)
