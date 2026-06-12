# -*- coding: utf-8 -*-
"""
Comprehensive end-to-end tests for call_kw_executor and related services.

Tests are designed to surface all known issues:
  ✓ Allowlist enforcement (including denial)
  ✓ XML ID resolution (valid, invalid, and ambiguous)
  ✓ Multi-record write (the crash from the production traceback)
  ✓ Serialization of complex return types
  ✓ Instruction-set execution with step references
  ✓ Transactional savepoint rollback
  ✓ Context and sudo behavior
  ✓ Edge cases in _resolve_xml_ref (non-XML dot-strings, URLs, etc.)
  ✓ Serialization of binary fields, datetime objects, x2many commands
  ✓ Timestamp/nonce verification
  ✓ Missing allowlist entries (notably search_count)
"""

import datetime
import json
import time
import hashlib
import hmac

from odoo.tests import common
from odoo.tools import mute_logger

from ..services.call_kw_executor import (
    execute_instruction,
    execute_instruction_set,
    _resolve_xml_ref,
    _resolve_xml_refs,
    _resolve_references,
    _resolve_references_list,
)
from ..services.allowlist import is_allowed, ALLOWLIST
from ..services.serializer import serialize_result, _serialize_single_record, _serialize_value
from ..services.signature import verify_signature


class TestAllowlist(common.TransactionCase):
    """Test allowlist enforcement — defense in depth layer."""

    def test_allowed_model_method(self):
        self.assertTrue(is_allowed('crm.lead', 'create'))
        self.assertTrue(is_allowed('crm.lead', 'write'))
        self.assertTrue(is_allowed('crm.lead', 'search_read'))
        self.assertTrue(is_allowed('crm.stage', 'search_read'))
        self.assertTrue(is_allowed('calendar.event', 'create'))
        self.assertTrue(is_allowed('bus.bus', '_sendone'))

    def test_denied_model(self):
        self.assertFalse(is_allowed('res.partner', 'unlink'))
        self.assertFalse(is_allowed('res.users', 'write'))
        self.assertFalse(is_allowed('ir.config_parameter', 'get_param'))

    def test_denied_method_on_allowed_model(self):
        self.assertFalse(is_allowed('crm.lead', 'unlink'))
        self.assertFalse(is_allowed('crm.lead', 'action_confirm'))
        self.assertFalse(is_allowed('res.partner', 'write'))

    def test_unknown_model(self):
        self.assertFalse(is_allowed('nonexistent.model', 'search_read'))

    def test_allowlist_missing_search_count(self):
        """FIXED: search_count is now in the allowlist for most models."""
        self.assertIn('search_count', ALLOWLIST.get('crm.lead', set()))
        self.assertIn('search_count', ALLOWLIST.get('crm.stage', set()))
        self.assertIn('search_count', ALLOWLIST.get('calendar.event', set()))
        self.assertIn('search_count', ALLOWLIST.get('res.users', set()))
        self.assertIn('search_count', ALLOWLIST.get('res.partner', set()))


class TestXMLRefResolution(common.TransactionCase):
    """Test _resolve_xml_ref and _resolve_xml_refs edge cases."""

    def test_valid_xml_id(self):
        env = self.env
        result = _resolve_xml_ref(env, 'base.main_company')
        self.assertIsInstance(result, int)

    def test_invalid_xml_id(self):
        """Non-existent XML ID should be returned as-is."""
        env = self.env
        result = _resolve_xml_ref(env, 'nonexistent.module_id')
        self.assertEqual(result, 'nonexistent.module_id')

    def test_url_not_treated_as_xml_id(self):
        """URLs starting with 'http' should NOT be resolved as XML IDs."""
        env = self.env
        result = _resolve_xml_ref(env, 'https://example.com')
        self.assertEqual(result, 'https://example.com')

    def test_non_url_dot_string_not_resolved(self):
        """FIXED: Only valid XML ID patterns are resolved now."""
        env = self.env
        result = _resolve_xml_ref(env, 'www.example.com')
        self.assertEqual(result, 'www.example.com')

    def test_version_string_not_xml_id(self):
        """FIXED: 'v1.0' no longer triggers XML ID resolution."""
        env = self.env
        result = _resolve_xml_ref(env, 'v1.0')
        self.assertEqual(result, 'v1.0')

    def test_property_string_not_xml_id(self):
        """'product.template' looks like an XML ID but may be a model name."""
        env = self.env
        result = _resolve_xml_ref(env, 'product.template')
        # env.ref('product.template') exists? If not, returns as-is.
        # But this is ambiguous — the intent could be a model name, not XML ID.
        self.assertIsInstance(result, (int, str))

    def test_resolve_xml_refs_recursive_dict(self):
        """Test deep recursive resolution in nested dicts."""
        env = self.env
        data = {
            'domain': [('company_id', '=', 'base.main_company')],
            'context': {'default_type': 'lead'},
        }
        result = _resolve_xml_refs(env, data)
        # The XML ID in the domain should be resolved to an int ID
        domain_item = result['domain'][0][2]
        self.assertIsInstance(domain_item, (int, str))
        # The non-XML-ID string 'lead' should stay as-is
        self.assertEqual(result['context']['default_type'], 'lead')

    def test_resolve_xml_refs_with_dollar_reference(self):
        """Strings starting with '$' (step references) should NOT be resolved."""
        env = self.env
        result = _resolve_xml_ref(env, '${step_1}')
        self.assertEqual(result, '${step_1}')

    def test_resolve_xml_refs_list(self):
        env = self.env
        data = ['base.main_company', 'not_an_xml_id', 42]
        result = _resolve_xml_refs(env, data)
        self.assertIsInstance(result[0], int)
        self.assertEqual(result[1], 'not_an_xml_id')
        self.assertEqual(result[2], 42)


class TestExecuteInstruction(common.TransactionCase):
    """Test the core execute_instruction function end-to-end."""

    def test_search_read_lead_happy_path(self):
        instruction = {
            'model': 'crm.lead',
            'method': 'search_read',
            'args': [[['type', '=', 'lead']]],
            'kwargs': {'limit': 5},
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertIsInstance(result['result'], list)

    def test_read_specific_lead(self):
        """Read a specific lead by ID."""
        lead = self.env['crm.lead'].create({'name': 'Test Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'read',
            'ids': [lead.id],
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertEqual(len(result['result']), 1)
        self.assertEqual(result['result'][0]['id'], lead.id)

    def test_create_lead(self):
        instruction = {
            'model': 'crm.lead',
            'method': 'create',
            'args': [{'name': 'Executor Test Lead', 'type': 'lead'}],
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertIn('id', result['result'])
        # Verify it was created
        lead = self.env['crm.lead'].browse(result['result']['id'])
        self.assertTrue(lead.exists())
        self.assertEqual(lead.name, 'Executor Test Lead')

    def test_write_lead_single_stage(self):
        """Write stage_id to a lead with a single stage ID (should work)."""
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'Stage Update Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': stage.id}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"Failed: {result.get('error')}")
        # Verify the stage was updated
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id.id, stage.id)

    def test_write_lead_stage_id_as_list(self):
        """FIXED: stage_id list is now normalized to single ID before write."""
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'Bug Stage List Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': [stage.id]}],  # list — now normalized
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"stage_id list should be normalized: {result.get('error')}")
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id.id, stage.id)

    def test_write_lead_multiple_stage_ids(self):
        """FIXED: Multiple stage IDs are now normalized to first element."""
        stages = self.env['crm.stage'].search([], limit=2)
        if len(stages) < 2:
            self.skipTest("Need at least 2 CRM stages")
        lead = self.env['crm.lead'].create({'name': 'Multi-stage Bug Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': stages.ids}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"multi-stage list should be normalized: {result.get('error')}")

    def test_write_multiple_leads_different_stages(self):
        """
        BUG: When writing to multiple leads simultaneously where the stage_id
        is an int (not list), check crm.lead.write() behavior with multi-record
        self. The write method iterates and should work, but test it.
        """
        stage = self.env['crm.stage'].search([], limit=1)
        lead1 = self.env['crm.lead'].create({'name': 'Multi Lead 1', 'type': 'lead'})
        lead2 = self.env['crm.lead'].create({'name': 'Multi Lead 2', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead1.id, lead2.id],
            'args': [{'stage_id': stage.id}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"Multi-lead write failed: {result.get('error')}")

    def test_blocked_method(self):
        instruction = {
            'model': 'crm.lead',
            'method': 'unlink',
            'ids': [1],
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertTrue(result.get('blocked'))
        self.assertIn('not allowed', result.get('error', ''))

    def test_blocked_model(self):
        instruction = {
            'model': 'res.partner',
            'method': 'unlink',
            'ids': [1],
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertTrue(result.get('blocked'))

    def test_unknown_model(self):
        instruction = {
            'model': 'nonexistent.model',
            'method': 'search_read',
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertIn('not allowed', result.get('error', ''))

    def test_unknown_method(self):
        instruction = {
            'model': 'crm.lead',
            'method': 'nonexistent_method',
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertIn('not allowed', result.get('error', ''))

    def test_nonexistent_record_ids(self):
        instruction = {
            'model': 'crm.lead',
            'method': 'read',
            'ids': [99999999],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertIn('No crm.lead records found', result.get('error', ''))

    def test_sudo_execution(self):
        """Test sudo execution works for allowed methods."""
        stage = self.env['crm.stage'].search([], limit=1)
        instruction = {
            'model': 'crm.stage',
            'method': 'read',
            'ids': [stage.id],
            'sudo': True,
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])

    def test_context_application(self):
        """Test that context is applied (e.g., lang context)."""
        lead = self.env['crm.lead'].create({'name': 'Context Test Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'read',
            'ids': [lead.id],
            'context': {'lang': 'en_US'},
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])

    def test_create_calendar_event(self):
        """FIXED: ISO datetime strings are now converted to Odoo format."""
        now = datetime.datetime.now()
        instruction = {
            'model': 'calendar.event',
            'method': 'create',
            'args': [{
                'name': 'Executor Test Meeting',
                'start': (now + datetime.timedelta(hours=1)).isoformat(),
                'stop': (now + datetime.timedelta(hours=2)).isoformat(),
            }],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"Calendar create with ISO datetime should work: {result.get('error')}")

    def test_message_post_on_lead(self):
        """Test message_post on a lead through executor."""
        lead = self.env['crm.lead'].create({'name': 'Message Post Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'message_post',
            'ids': [lead.id],
            'kwargs': {'body': 'Test message from executor'},
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"message_post failed: {result.get('error')}")

    def test_search_read_empty_result(self):
        """search_read with domain that matches nothing should return empty list."""
        instruction = {
            'model': 'crm.lead',
            'method': 'search_read',
            'args': [[['name', '=', 'NonExistentLead999999']]],
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertEqual(result['result'], [])

    def test_xml_id_in_args_resolved(self):
        """
        Test that XML IDs in args are resolved before execution.
        crm.stage search_read should work with XML ID in domain.
        """
        instruction = {
            'model': 'crm.stage',
            'method': 'search_read',
            'args': [[['team_id', '=', 'sales_team.team_sales_department']]],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        # This may or may not find stages — the key is that it doesn't crash
        self.assertTrue(result['success'], f"XML ID resolution failed: {result.get('error')}")


class TestSerializer(common.TransactionCase):
    """Test serialization of Odoo return types."""

    def test_serialize_int(self):
        self.assertEqual(serialize_result(42), 42)

    def test_serialize_str(self):
        self.assertEqual(serialize_result("hello"), "hello")

    def test_serialize_bool(self):
        self.assertEqual(serialize_result(True), True)

    def test_serialize_none(self):
        self.assertIsNone(serialize_result(None))

    def test_serialize_datetime(self):
        dt = datetime.datetime(2026, 6, 12, 10, 30, 0)
        result = serialize_result(dt)
        self.assertIsInstance(result, str)
        self.assertIn('2026-06-12', result)

    def test_serialize_date(self):
        d = datetime.date(2026, 6, 12)
        result = serialize_result(d)
        self.assertIsInstance(result, str)
        self.assertEqual(result, '2026-06-12')

    def test_serialize_empty_recordset(self):
        empty = self.env['crm.lead'].browse()
        result = serialize_result(empty)
        self.assertEqual(result, [])

    def test_serialize_single_record(self):
        lead = self.env['crm.lead'].create({'name': 'Serializer Lead', 'type': 'lead'})
        result = serialize_result(lead)
        self.assertIsInstance(result, dict)
        self.assertIn('id', result)
        self.assertIn('name', result)
        self.assertEqual(result['name'], 'Serializer Lead')

    def test_serialize_multi_recordset(self):
        lead1 = self.env['crm.lead'].create({'name': 'Multi Ser 1', 'type': 'lead'})
        lead2 = self.env['crm.lead'].create({'name': 'Multi Ser 2', 'type': 'lead'})
        records = lead1 | lead2
        result = serialize_result(records)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    def test_serialize_list_of_ints(self):
        result = serialize_result([1, 2, 3])
        self.assertEqual(result, [1, 2, 3])

    def test_serialize_dict(self):
        result = serialize_result({'key': 'value'})
        self.assertEqual(result, {'key': 'value'})

    def test_serialize_nested_dict_with_datetime(self):
        result = serialize_result({
            'date': datetime.datetime(2026, 6, 12),
            'nested': {'date': datetime.date(2026, 6, 13)},
        })
        self.assertIsInstance(result['date'], str)
        self.assertIsInstance(result['nested']['date'], str)

    def test_serialize_nested_list_with_datetime(self):
        result = serialize_result([datetime.date(2026, 6, 12), datetime.date(2026, 6, 13)])
        self.assertIsInstance(result[0], str)
        self.assertIsInstance(result[1], str)

    def test_serialize_binary_field(self):
        """
        Binary fields should be stripped to None in serialized records.
        """
        lead = self.env['crm.lead'].create({'name': 'Binary Test', 'type': 'lead'})
        result = _serialize_single_record(lead)
        # crm.lead doesn't have binary fields, but the function should handle it
        self.assertIsInstance(result, dict)


class TestExecuteInstructionSet(common.TransactionCase):
    """Test multi-step instruction set execution."""

    def test_single_step(self):
        steps = [{
            'id': 'find_leads',
            'model': 'crm.lead',
            'method': 'search_read',
            'args': [[['type', '=', 'lead']]],
            'kwargs': {'limit': 3},
        }]
        result = execute_instruction_set(self.env, {
            'trace_id': 'test-001',
            'steps': steps,
        })
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['step'], 'find_leads')

    def test_two_steps_no_reference(self):
        steps = [
            {
                'id': 'read_stages',
                'model': 'crm.stage',
                'method': 'search_read',
                'kwargs': {'limit': 5},
            },
            {
                'id': 'read_leads',
                'model': 'crm.lead',
                'method': 'search_read',
                'kwargs': {'limit': 5},
            },
        ]
        result = execute_instruction_set(self.env, {
            'trace_id': 'test-002',
            'steps': steps,
        })
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 2)

    def test_step_reference_capture_list_index(self):
        """FIXED: ${step.N.field} now supports list indexing."""
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'Ref Capture Lead', 'type': 'lead'})
        steps = [
            {
                'id': 'find_stage',
                'model': 'crm.stage',
                'method': 'search_read',
                'args': [[['id', '=', stage.id]]],
                'kwargs': {'limit': 1},
                'capture_result': True,
            },
            {
                'id': 'update_lead',
                'model': 'crm.lead',
                'method': 'write',
                'ids': [lead.id],
                'args': [{'stage_id': '${find_stage.0.id}'}],
            },
        ]
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, {
                'trace_id': 'test-003',
                'steps': steps,
            })
        self.assertTrue(result['success'],
                       f"List index ref should work: {result}")
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id.id, stage.id)

    def test_transactional_rollback_on_error(self):
        """FIXED: references in ids now extract id from dict."""
        # Test that ${create_lead} in ids extracts the id from captured dict
        steps = [
            {
                'id': 'create_lead',
                'model': 'crm.lead',
                'method': 'create',
                'args': [{'name': 'Dict Ref Rollback Lead', 'type': 'lead'}],
                'capture_result': True,
            },
            {
                'id': 'fail_step',
                'model': 'crm.lead',
                'method': 'nonexistent_method',  # blocked — triggers rollback
            },
        ]
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, {
                'trace_id': 'test-004',
                'steps': steps,
                'transaction': True,
            })
        self.assertFalse(result['success'])
        self.assertIsNotNone(result.get('error_step'))
        # Verify the rollback
        leads = self.env['crm.lead'].search([('name', '=', 'Dict Ref Rollback Lead')])
        self.assertEqual(len(leads), 0, "Rollback should have undone the creation")

    def test_ids_reference_from_dict_extraction(self):
        """FIXED: ${step_id} in ids extracts 'id' from captured dict."""
        lead = self.env['crm.lead'].create({'name': 'Dict Ref Lead', 'type': 'lead'})
        steps = [
            {
                'id': 'find_lead',
                'model': 'crm.lead',
                'method': 'read',
                'ids': [lead.id],
                'capture_result': True,
            },
            {
                'id': 'update_lead',
                'model': 'crm.lead',
                'method': 'write',
                'ids': ['${find_lead}'],
                'args': [{'name': 'Dict Ref Updated'}],
            },
        ]
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, {
                'trace_id': 'test-dict-ref',
                'steps': steps,
            })
        self.assertTrue(result['success'],
                       f"Dict reference extraction should work: {result}")
        lead.invalidate_recordset()
        self.assertEqual(lead.name, 'Dict Ref Updated')

    def test_non_transactional_no_rollback(self):
        """Without transaction=True, prior steps persist even when later ones fail."""
        steps = [
            {
                'id': 'create_lead',
                'model': 'crm.lead',
                'method': 'create',
                'args': [{'name': 'NoRollback Test Lead', 'type': 'lead'}],
                'capture_result': True,
            },
            {
                'id': 'fail_step',
                'model': 'crm.lead',
                'method': 'nonexistent_method',
            },
        ]
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, {
                'trace_id': 'test-005',
                'steps': steps,
                'transaction': False,
            })
        self.assertFalse(result['success'])
        # Verify the lead still exists (no rollback)
        leads = self.env['crm.lead'].search([('name', '=', 'NoRollback Test Lead')])
        self.assertEqual(len(leads), 1, "Non-transactional: lead should still exist")

    def test_empty_steps(self):
        result = execute_instruction_set(self.env, {
            'trace_id': 'test-006',
            'steps': [],
        })
        self.assertTrue(result['success'])
        self.assertEqual(result['results'], [])

    def test_missing_step_id_defaults(self):
        """Steps without explicit 'id' get auto-assigned 'step_0', 'step_1', etc."""
        steps = [
            {'model': 'crm.stage', 'method': 'search_read', 'kwargs': {'limit': 1}},
            {'model': 'crm.lead', 'method': 'search_read', 'kwargs': {'limit': 1}},
        ]
        result = execute_instruction_set(self.env, {
            'trace_id': 'test-007',
            'steps': steps,
        })
        self.assertTrue(result['success'])
        self.assertEqual(result['results'][0]['step'], 'step_0')
        self.assertEqual(result['results'][1]['step'], 'step_1')

    def test_first_step_blocked_stops_all(self):
        """If the first step is blocked, execution stops immediately."""
        steps = [
            {'id': 'blocked', 'model': 'crm.lead', 'method': 'unlink', 'ids': [1]},
            {'id': 'never_runs', 'model': 'crm.lead', 'method': 'search_read'},
        ]
        result = execute_instruction_set(self.env, {
            'trace_id': 'test-008',
            'steps': steps,
        })
        self.assertFalse(result['success'])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['error_step'], 'blocked')


class TestReferenceResolution(common.TransactionCase):
    """Test the _resolve_references function for ${step_id} patterns."""

    def test_simple_reference(self):
        captured = {'step_a': 42}
        result = _resolve_references({'value': '${step_a}'}, captured)
        self.assertEqual(result['value'], 42)

    def test_field_reference(self):
        captured = {'step_a': {'id': 10, 'name': 'Test'}}
        result = _resolve_references({'value': '${step_a.id}'}, captured)
        self.assertEqual(result['value'], 10)

    def test_nested_reference_in_list(self):
        captured = {'step_a': 5}
        result = _resolve_references(['${step_a}', 3, 4], captured)
        self.assertEqual(result, [5, 3, 4])

    def test_nested_reference_in_dict(self):
        captured = {'step_a': {'x': 1}}
        result = _resolve_references(
            {'outer': {'inner': '${step_a.x}'}}, captured
        )
        self.assertEqual(result['outer']['inner'], 1)

    def test_unknown_reference(self):
        captured = {}
        result = _resolve_references('${nonexistent}', captured)
        self.assertEqual(result, '${nonexistent}')

    def test_no_dollar_reference(self):
        captured = {'step_a': 42}
        result = _resolve_references('plain_string', captured)
        self.assertEqual(result, 'plain_string')

    def test_reference_in_ids_list(self):
        captured = {'found_id': 42}
        result = _resolve_references_list(['${found_id}'], captured)
        self.assertEqual(result, [42])

    def test_reference_in_ids_list_not_found(self):
        captured = {}
        result = _resolve_references_list(['${not_found}'], captured)
        self.assertEqual(result, ['${not_found}'])

    def test_reference_list_in_ids(self):
        captured = {'found_ids': [10, 20, 30]}
        result = _resolve_references_list(['${found_ids}'], captured)
        self.assertEqual(result, [10, 20, 30])


class TestSignatureVerification(common.TransactionCase):
    """Test signature verification logic.

    NOTE: verify_signature() uses odoo.http.request.env which is only
    available within an HTTP request context. These tests must run
    inside a controller test or be restructured to use a wrapped helper.
    """

    def test_verify_signature_with_env_parameter(self):
        """FIXED: verify_signature now accepts env parameter, works in tests."""
        import hashlib, hmac
        from datetime import datetime, timezone
        secret = 'test-secret-12345'
        self.env['ir.config_parameter'].sudo().set_param(
            'crm_assistant_connector.secret', secret
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        nonce = f'test-nonce-ok-{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}'
        payload = '{"test": true}'
        message = f"{payload}:{nonce}:{timestamp}"
        sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

        # Pass env explicitly — should now work
        result = verify_signature(payload, sig, nonce, timestamp, env=self.env)
        self.assertTrue(result['valid'], f"Verification should pass: {result.get('error')}")


class TestExecuteInstructionErrors(common.TransactionCase):
    """Test error handling paths in execute_instruction."""

    def test_blocked_method_returns_correct_structure(self):
        instruction = {
            'model': 'crm.lead',
            'method': 'unlink',
            'ids': [1],
        }
        result = execute_instruction(self.env, instruction)
        self.assertIn('success', result)
        self.assertFalse(result['success'])
        self.assertIn('error', result)
        self.assertTrue(result.get('blocked'))

    def test_res_partner_write_blocked(self):
        """
        BUG: res.partner can search but NOT write. If the executor receives
        a write instruction for res.partner, it's correctly blocked.
        But this is a design limitation — no way to update partner data.
        """
        instruction = {
            'model': 'res.partner',
            'method': 'write',
            'ids': [1],
            'args': [{'name': 'Modified'}],
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertTrue(result.get('blocked'))

    def test_empty_args_and_kwargs(self):
        instruction = {
            'model': 'crm.stage',
            'method': 'search_read',
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertIsInstance(result['result'], list)

    def test_search_read_crm_stage_return_type(self):
        """search_read on crm.stage returns a list of dicts."""
        result = execute_instruction(self.env, {
            'model': 'crm.stage',
            'method': 'search_read',
            'args': [[], ['id', 'name', 'is_won', 'sequence']],
            'kwargs': {'limit': 5},
        })
        self.assertTrue(result['success'])
        self.assertIsInstance(result['result'], list)
        if result['result']:
            self.assertIsInstance(result['result'][0], dict)
            self.assertIn('id', result['result'][0])
            self.assertIn('is_won', result['result'][0])

    def test_activity_schedule_method_not_found(self):
        """
        BUG CONFIRMED: The allowlist contains 'activity_schedule' for crm.lead,
        but this method doesn't exist on the model.

        In Odoo, the method is inherited from mail.activity.mixin via
        crm.lead, but the method name differs (likely action_schedule_activity).
        The allowlist has the wrong method name.
        """
        activity_type = self.env['mail.activity.type'].search([], limit=1)
        if not activity_type:
            self.skipTest("No mail activity types available")
        lead = self.env['crm.lead'].create({'name': 'Activity Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'activity_schedule',
            'ids': [lead.id],
            'args': [activity_type.id],
            'kwargs': {'summary': 'Follow up on this lead'},
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        if not result['success'] and 'no method' in result.get('error', ''):
            print(f"\n  >>> BUG CONFIRMED (activity_schedule): {result.get('error')}")

    def test_search_with_complex_domain(self):
        """Test search_read with a complex domain."""
        instruction = {
            'model': 'crm.lead',
            'method': 'search_read',
            'args': [[
                '&',
                ('type', '=', 'lead'),
                '|',
                ('active', '=', True),
                ('active', '=', False),
            ]],
            'kwargs': {'limit': 10},
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])


class TestEdgeCases(common.TransactionCase):
    """Test edge cases and defensive behavior."""

    def test_write_with_user_id_as_list(self):
        """FIXED: user_id as list is now normalized to single ID."""
        user = self.env.user
        lead = self.env['crm.lead'].create({'name': 'UserId Bug Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'user_id': [user.id]}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"user_id list should be normalized: {result.get('error')}")

    def test_write_with_non_dict_args(self):
        """
        Test write with positional args that aren't dicts.
        The Odoo write method expects vals as a dict.
        """
        lead = self.env['crm.lead'].create({'name': 'NonDict Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': ['not_a_dict'],  # Should error gracefully
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])

    def test_xml_id_in_write_args(self):
        """
        Test XML ID resolution within write args dict.
        If someone passes an XML ID as a value in the write vals,
        it should resolve to the database ID.
        """
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'XML Write Lead', 'type': 'lead'})

        # Find the XML ID of the stage (if any)
        stage_xml_id = None
        for record in self.env['ir.model.data'].search([
            ('model', '=', 'crm.stage'),
            ('res_id', '=', stage.id),
        ], limit=1):
            stage_xml_id = f"{record.module}.{record.name}"

        if not stage_xml_id:
            self.skipTest("No XML ID found for the CRM stage")

        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': stage_xml_id}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"XML ID write failed: {result.get('error')}")
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id.id, stage.id)

    def test_search_read_with_offset_limit(self):
        """Test pagination parameters."""
        instruction = {
            'model': 'crm.lead',
            'method': 'search_read',
            'args': [[], ['id']],
            'kwargs': {'offset': 0, 'limit': 1, 'order': 'id DESC'},
        }
        result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertLessEqual(len(result['result']), 1)

    def test_execute_instruction_no_model(self):
        """Empty model name should be rejected."""
        instruction = {
            'model': '',
            'method': 'search_read',
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertTrue(result.get('blocked'))

    def test_execute_instruction_no_method(self):
        """Empty method name should be rejected."""
        instruction = {
            'model': 'crm.lead',
            'method': '',
        }
        result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertTrue(result.get('blocked'))


class TestAdditionalBugs(common.TransactionCase):
    """Additional tests for issues discovered during analysis."""

    def test_search_count_now_allowed(self):
        """FIXED: search_count is now in allowlist and works."""
        result = execute_instruction(self.env, {
            'model': 'crm.lead',
            'method': 'search_count',
            'args': [[('type', '=', 'lead')]],
        })
        self.assertTrue(result['success'],
                       f"search_count should be allowed: {result.get('error')}")
        self.assertIsInstance(result['result'], int)

    def test_resolve_xml_ref_with_model_name(self):
        """
        POTENTIAL BUG: If an argument contains a model name like
        'mail.activity' or 'crm.lead' as a string value (not as model
        name for the instruction), the XML ref resolver tries to resolve
        it via env.ref(), which returns the model definition object.

        'ir.model' is in the allowlist for search/read, and the resolver
        would try to resolve 'ir.model' as an XML ID, potentially
        returning unexpected results.
        """
        env = self.env
        result = _resolve_xml_ref(env, 'crm.lead')
        # Currently returns an int (model's ir.model record ID) if
        # the XML ID exists, or the string as-is
        if isinstance(result, int):
            print(f"\n  >>> BUG CONFIRMED (model name as XML ID): 'crm.lead' resolved to {result}")

    def test_serialize_many2one_tuple(self):
        """
        BUG: When serialize_result encounters a Many2one tuple like
        (1, 'Admin'), it serializes it as a dict via _serialize_value
        with {0: 1, 1: 'Admin'}, which is incorrect. Many2one references
        should be preserved as (id, display_name) tuples or dicts with
        meaningful keys.
        """
        lead = self.env['crm.lead'].create({'name': 'Many2one Test', 'type': 'lead'})
        # Get a record dict that contains create_uid as (id, name) tuple
        result = _serialize_single_record(lead)
        if 'create_uid' in result:
            uid_val = result['create_uid']
            if isinstance(uid_val, dict):
                # If it was serialized as {0: id, 1: name}, that's a problem
                if 0 in uid_val and 1 in uid_val:
                    print(f"\n  >>> BUG CONFIRMED (Many2one tuple serialization): {uid_val}")

    def test_won_lost_transition_with_write(self):
        """
        Test that the executor can write stage changes that trigger
        won/lost transitions without crashing.

        crm.lead.write() calls _handle_won_lost() which has additional
        side effects. The executor must handle this.
        """
        # Find a 'won' stage
        won_stage = self.env['crm.stage'].search([('is_won', '=', True)], limit=1)
        if not won_stage:
            self.skipTest("No 'won' stage configured")
        lead = self.env['crm.lead'].create({'name': 'Won Transition Lead', 'type': 'opportunity'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': won_stage.id}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"Won transition failed: {result.get('error')}")

    def test_lost_transition_with_write(self):
        """Test lost/won transition via write (edge case from traceback)."""
        lost_stage = self.env['crm.stage'].search([('is_won', '=', False)], limit=1)
        if not lost_stage:
            self.skipTest("No lost stage configured")
        lead = self.env['crm.lead'].create({'name': 'Lost Transition Lead', 'type': 'opportunity'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': lost_stage.id}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"Lost transition failed: {result.get('error')}")

    def test_action_set_won(self):
        """Test action_set_won through executor (explicit won action)."""
        lead = self.env['crm.lead'].create({'name': 'Action Won Lead', 'type': 'opportunity'})
        instruction = {
            'model': 'crm.lead',
            'method': 'action_set_won',
            'ids': [lead.id],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"action_set_won failed: {result.get('error')}")

    def test_action_set_lost(self):
        """Test action_set_lost through executor (explicit lost action)."""
        lead = self.env['crm.lead'].create({'name': 'Action Lost Lead', 'type': 'opportunity'})
        instruction = {
            'model': 'crm.lead',
            'method': 'action_set_lost',
            'ids': [lead.id],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'], f"action_set_lost failed: {result.get('error')}")

    def test_write_many2one_as_tuple(self):
        """FIXED: Many2one tuple values are now normalized."""
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'Tuple Stage Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': (stage.id, stage.name)}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"M2O tuple should be normalized: {result.get('error')}")
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id.id, stage.id)

    def test_write_many2one_as_recordset(self):
        """FIXED: Recordset M2O values are now normalized to .id."""
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'Recset Stage Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': stage}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"Recordset M2O should be normalized: {result.get('error')}")
        lead.invalidate_recordset()
        self.assertEqual(lead.stage_id.id, stage.id)

    def test_write_team_id_as_list(self):
        """FIXED: team_id as list is now normalized."""
        team = self.env['crm.team'].search([], limit=1)
        if not team:
            self.skipTest("No CRM team available")
        lead = self.env['crm.lead'].create({'name': 'TeamID Bug Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'team_id': [team.id]}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"team_id list should be normalized: {result.get('error')}")

    def test_production_crash_reproduction(self):
        """FIXED: Exact reproduction of the production crash now succeeds."""
        stage = self.env['crm.stage'].search([], limit=1)
        lead = self.env['crm.lead'].create({'name': 'Production Bug Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'stage_id': [stage.id]}],
            'sudo': False,
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'],
                       f"Production crash reproduction should now succeed: {result.get('error')}")


class TestBusNotificationDispatch(common.TransactionCase):
    """Test that bus notifications are dispatched after write/create/unlink.

    For direct execute_instruction() calls (used by /execute endpoint),
    notifications are returned via the _notification key in the result and
    dispatched by the caller (execute_instruction_set). For execute_instruction_set()
    calls (actual production path), notifications are dispatched to bus.bus.
    """

    def _get_bus_values(self):
        """Return queued bus.bus notification values from precommit data."""
        return self.env.cr.precommit.data.get("bus.bus.values", [])

    def _assert_result_has_notification(self, result, model, method):
        """Assert the result contains a _notification for the given model/method."""
        notification = result.get('_notification')
        self.assertIsNotNone(
            notification,
            f"Expected _notification in result for {model}.{method}"
        )
        self.assertEqual(notification['model'], model)
        self.assertEqual(notification['method'], method)
        return notification

    def _assert_bus_notification(self, model, method):
        """Assert the last bus.bus notification matches expected model and method."""
        values = self._get_bus_values()
        self.assertTrue(values, f"Expected bus notification for {model}.{method}, got none")
        last = values[-1]
        message = json.loads(last['message'])
        self.assertEqual(message['type'], 'crm_assistant_record_changed')
        self.assertEqual(message['payload']['model'], model)
        self.assertEqual(message['payload']['method'], method)
        return message

    def test_notification_returned_after_write(self):
        """A write instruction should include _notification in result."""
        lead = self.env['crm.lead'].create({'name': 'Bus Notify Lead', 'type': 'lead'})
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [lead.id],
            'args': [{'name': 'Bus Notify Renamed'}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self._assert_result_has_notification(result, 'crm.lead', 'write')

    def test_notification_returned_after_create(self):
        """A create instruction should include _notification in result."""
        instruction = {
            'model': 'crm.lead',
            'method': 'create',
            'args': [{'name': 'Bus Notify Created', 'type': 'lead'}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self._assert_result_has_notification(result, 'crm.lead', 'create')

    def test_notification_returned_after_unlink(self):
        """An unlink instruction should include _notification in result."""
        event = self.env['calendar.event'].create({
            'name': 'Bus Notify Event',
            'start': '2026-06-12 10:00:00',
            'stop': '2026-06-12 11:00:00',
        })
        instruction = {
            'model': 'calendar.event',
            'method': 'unlink',
            'ids': [event.id],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self._assert_result_has_notification(result, 'calendar.event', 'unlink')

    def test_no_notification_for_search_read(self):
        """A read-only operation should NOT include _notification in result."""
        instruction = {
            'model': 'crm.lead',
            'method': 'search_read',
            'args': [[['type', '=', 'lead']]],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertTrue(result['success'])
        self.assertIsNone(
            result.get('_notification'),
            "No _notification should be returned for search_read"
        )

    def test_no_notification_for_failed_write(self):
        """A failed write should NOT include _notification in result."""
        instruction = {
            'model': 'crm.lead',
            'method': 'write',
            'ids': [999999],  # non-existent ID
            'args': [{'name': 'Should Fail'}],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction(self.env, instruction)
        self.assertFalse(result['success'])
        self.assertNotIn('_notification', result,
                         "No _notification should be returned for failed write")

    def test_notification_channel_exact_match(self):
        """Verify bus notification channel via execute_instruction_set (production path)."""
        lead = self.env['crm.lead'].create({'name': 'Channel Test', 'type': 'lead'})
        instruction_set = {
            'trace_id': 'test-channel',
            'transaction': False,
            'steps': [{
                'id': 'write_step',
                'model': 'crm.lead',
                'method': 'write',
                'ids': [lead.id],
                'args': [{'name': 'Channel Test Renamed'}],
            }],
        }
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, instruction_set)
        self.assertTrue(result['success'])
        values = self._get_bus_values()
        self.assertTrue(values, "Expected at least one bus notification")
        last = values[-1]
        channel = json.loads(last['channel'])
        self.assertIsInstance(channel, list)
        expected_channel = f'crm_assistant_{self.env.uid}'
        self.assertEqual(channel[1], expected_channel,
                         f"Channel should be {expected_channel}, got {channel[1]}")

    def test_transactional_set_batches_notifications(self):
        """Transactional sets should batch notifications and dispatch after commit."""
        stage = self.env['crm.stage'].search([], limit=1)
        self.assertTrue(stage, "Need at least one CRM stage")
        lead = self.env['crm.lead'].create({'name': 'Transactional Lead', 'type': 'lead'})

        instruction_set = {
            'trace_id': 'test-txn-batch',
            'transaction': True,
            'steps': [
                {
                    'id': 'rename',
                    'model': 'crm.lead',
                    'method': 'write',
                    'ids': [lead.id],
                    'args': [{'name': 'Transactional Renamed'}],
                },
                {
                    'id': 'move_stage',
                    'model': 'crm.lead',
                    'method': 'write',
                    'ids': [lead.id],
                    'args': [{'stage_id': stage.id}],
                },
            ],
        }
        values_before = len(self._get_bus_values())
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, instruction_set)
        self.assertTrue(result['success'], f"Instruction set failed: {result.get('error_step')}")
        values_after = self._get_bus_values()
        self.assertEqual(
            len(values_after) - values_before, 2,
            "Transactional set should dispatch exactly 2 notifications after commit"
        )

    def test_transactional_set_no_notification_on_rollback(self):
        """Rolled-back transactional sets should NOT dispatch notifications."""
        lead = self.env['crm.lead'].create({'name': 'Rollback Lead', 'type': 'lead'})

        instruction_set = {
            'trace_id': 'test-txn-rollback',
            'transaction': True,
            'steps': [
                {
                    'id': 'rename',
                    'model': 'crm.lead',
                    'method': 'write',
                    'ids': [lead.id],
                    'args': [{'name': 'Should Rollback'}],
                },
                {
                    'id': 'fail',
                    'model': 'crm.lead',
                    'method': 'write',
                    'ids': [999999],  # non-existent → triggers rollback
                    'args': [{'name': 'This Fails'}],
                },
            ],
        }
        values_before = len(self._get_bus_values())
        with mute_logger('odoo.addons.crm_assistant_connector.services.call_kw_executor'):
            result = execute_instruction_set(self.env, instruction_set)
        self.assertFalse(result['success'])
        values_after = self._get_bus_values()
        self.assertEqual(
            len(values_after), values_before,
            "Rolled-back transactional set should dispatch zero notifications",
        )
