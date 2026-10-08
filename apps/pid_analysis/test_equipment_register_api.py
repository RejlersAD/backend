"""Functional checks for durable Equipment Register draft commands."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.project_organizer.models import Project

from .equipment_metadata import (
    _harvest_relationships,
    _source_supported_enrichment,
    build_equipment_metadata,
    enrich_equipment_metadata,
    equipment_master_view,
    normalise_equipment_metadata,
    resolve_batch_cross_references,
)
from .equipment_vision import render_page_images, vision_enrich_equipment
from .models import EquipmentItemChange, EquipmentRegister, EquipmentRevision


urlpatterns = [path('api/v1/pid/', include('apps.pid_analysis.urls'))]


@override_settings(ROOT_URLCONF=__name__)
class EquipmentRegisterApiTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username='equipment-owner', email='owner@example.test')
        self.other = users.create_user(username='equipment-other', email='other@example.test')
        self.project = Project.objects.create(
            name='Synthetic process project', code='SYN-01', discipline='Process',
            created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.import_url = '/api/v1/pid/equipment-registers/import-extraction/'
        self.current_url = f'/api/v1/pid/equipment-registers/current/?project_id={self.project.pk}'
        self.payload = {
            'project_id': str(self.project.pk),
            'source_upload_id': 'EQ-SYNTHETIC-001',
            'source_files': ['synthetic-pid.pdf'],
            'drawing_ref': 'PID-SYN-001',
            'items': [{
                'tag': 'V-101', 'description': 'Test separator',
                'equipment_type': 'Vessel', 'design_pressure_max': '180',
                'oper_pressure': '150', 'moc': 'CS',
            }],
        }

    def import_register(self):
        response = self.client.post(self.import_url, self.payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_import_is_durable_and_retry_is_idempotent(self):
        response = self.import_register()
        self.assertEqual(response.data['register_number'], 'EL-SYN-01')
        self.assertEqual(response.data['revision']['number'], 1)
        self.assertEqual(response.data['items'][0]['tag'], 'V-101')
        metadata = response.data['items'][0]['metadata']
        self.assertEqual(metadata['pid_information']['drawing_no'], 'PID-SYN-001')
        self.assertEqual(metadata['equipment_record']['tag_number'], 'V-101')
        self.assertEqual(metadata['engineering_specifications']['design_pressure'], '180')
        self.assertIn('Equipment Datasheet', metadata['validation_findings']['recommended_sources'])
        self.assertEqual(EquipmentRegister.objects.count(), 1)
        self.assertEqual(EquipmentRevision.objects.count(), 1)
        self.assertEqual(EquipmentItemChange.objects.filter(field='__row__').count(), 1)

        retry = self.client.post(self.import_url, self.payload, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(EquipmentRevision.objects.count(), 1)

        current = self.client.get(self.current_url)
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.data['items'][0]['description'], 'Test separator')

    def test_item_update_rejects_stale_and_immutable_revisions(self):
        imported = self.import_register().data
        register_id = imported['id']
        item_id = imported['items'][0]['id']
        item_url = f'/api/v1/pid/equipment-registers/{register_id}/items/{item_id}/'
        updated = self.client.patch(item_url, {
            'expected_revision_version': 1,
            'set': {'description': 'Reviewed separator'},
            'reason': 'Corrected against source.',
        }, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['revision']['version'], 2)
        self.assertEqual(updated.data['items'][0]['description'], 'Reviewed separator')
        self.assertEqual(
            updated.data['items'][0]['metadata']['equipment_record']['description'],
            'Reviewed separator',
        )
        self.assertEqual(
            updated.data['items'][0]['metadata']['pid_information']['equipment_name'],
            'Reviewed separator',
        )
        self.assertEqual(updated.data['items'][0]['status'], 'Changed')
        self.assertEqual(updated.data['revision']['summary']['changed_items'], 1)
        self.assertTrue(EquipmentItemChange.objects.filter(
            field='description', old_value='Test separator', new_value='Reviewed separator',
        ).exists())

        stale = self.client.patch(item_url, {
            'expected_revision_version': 1,
            'set': {'description': 'Stale overwrite'},
        }, format='json')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data['code'], 'stale_revision')

        revision = EquipmentRevision.objects.get(pk=updated.data['revision']['id'])
        revision.status = EquipmentRevision.Status.SUBMITTED
        revision.is_immutable = True
        revision.save(update_fields=['status', 'is_immutable'])
        immutable = self.client.patch(item_url, {
            'expected_revision_version': 2,
            'set': {'description': 'Forbidden overwrite'},
        }, format='json')
        self.assertEqual(immutable.status_code, 409)

        history = self.client.get(
            f'/api/v1/pid/equipment-registers/{register_id}/changes/?item_id={item_id}'
        )
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data['total'], 2)
        self.assertEqual(history.data['changes'][0]['field'], 'description')
        self.assertEqual(history.data['changes'][0]['changed_by'], 'equipment-owner')

    def test_other_user_cannot_read_or_import_against_project(self):
        self.import_register()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.current_url).status_code, 403)
        denied = self.client.post(self.import_url, {
            **self.payload, 'source_upload_id': 'EQ-DENIED-002',
        }, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(EquipmentRevision.objects.count(), 1)
        register = EquipmentRegister.objects.get()
        self.assertEqual(
            self.client.get(f'/api/v1/pid/equipment-registers/{register.pk}/changes/').status_code,
            403,
        )

    def test_invalid_or_duplicate_rows_do_not_create_a_register(self):
        missing_tag = self.client.post(self.import_url, {
            **self.payload, 'items': [{'description': 'Missing equipment tag'}],
        }, format='json')
        self.assertEqual(missing_tag.status_code, 400)

        duplicate = self.client.post(self.import_url, {
            **self.payload,
            'items': [
                self.payload['items'][0],
                {**self.payload['items'][0], 'tag': 'v-101'},
            ],
        }, format='json')
        self.assertEqual(duplicate.status_code, 400)

        invalid_metadata = self.client.post(self.import_url, {
            **self.payload,
            'source_upload_id': 'EQ-INVALID-METADATA',
            'items': [{**self.payload['items'][0], 'metadata': {'uncontrolled': {}}}],
        }, format='json')
        self.assertEqual(invalid_metadata.status_code, 400)
        self.assertEqual(EquipmentRegister.objects.count(), 0)

    def test_metadata_detects_source_backed_connected_tags(self):
        metadata = build_equipment_metadata({
            **self.payload['items'][0],
            'pid_no': 'PID-SYN-001',
            'source_locator': {'filename': 'synthetic-pid.pdf', 'page': 2},
            'service_fluid': 'Sour Gas',
        }, document_text=(
            'V-101 CONNECTED TO PSV-8002 SDV-8003 PT-8003A '
            'LT-8001A FIC-8002 TG-8003 VIA LINE 10-PG-1001\n'
            'V-101 CONTROLLED BY FCV-8001 PAHH-8001 PSHH-8001\n'
            'V-101 DISCHARGES TO P-202; CONTINUED ON P&ID PID-2002\n'
            'V-101 TO P-202 VIA 8"-PG-2222; SEE DWG PJ6-EXD-MRI-0023\n'
            'V-101 INTERNAL: MIST ELIMINATOR\n'
            'V-101 STREAM: SOUR GAS\n'
            'V-101 NOTE: VERIFY ELEVATION'
        ))
        self.assertEqual(
            metadata['connected_safety_equipment']['pressure_safety_valves'],
            ['PSV-8002'],
        )
        self.assertEqual(metadata['main_process_instruments']['pressure_instruments'], ['PT-8003A'])
        self.assertEqual(metadata['connected_lines'][0]['line_tag'], '10-PG-1001')
        self.assertEqual(metadata['connected_lines'][1]['line_tag'], '8"-PG-2222')
        master = metadata['relationships']
        self.assertEqual(master['connected_equipment'][0]['tag'], 'P-202')
        self.assertEqual(master['control_valves'][0]['tag'], 'FCV-8001')
        self.assertEqual(master['alarms'][0]['tag'], 'PAHH-8001')
        self.assertEqual(master['trips'][0]['tag'], 'PSHH-8001')
        self.assertEqual(master['cross_pid_references'][0]['tag'], 'PID-2002')
        self.assertEqual(master['cross_pid_references'][1]['tag'], 'PJ6-EXD-MRI-0023')
        self.assertEqual(master['internals'][0]['description'], 'MIST ELIMINATOR')
        self.assertEqual(master['process_streams'][0]['description'], 'SOUR GAS')
        self.assertEqual(master['engineering_notes'][0]['page'], 2)
        self.assertEqual(master['engineering_notes'][0]['filename'], 'synthetic-pid.pdf')
        self.assertEqual(metadata['source_documents'][0]['drawing_no'], 'PID-SYN-001')

    def test_proximity_does_not_invent_connected_equipment(self):
        metadata = build_equipment_metadata({
            **self.payload['items'][0], 'pid_no': 'PID-SYN-001',
            'line_connections': ['10-PG-1001'],
        }, document_text='V-101 TEST SEPARATOR PSV-8002 PT-8003A\nP-202 NEARBY')
        self.assertEqual(metadata['relationships'], {})
        self.assertEqual(metadata['connected_lines'], [])
        self.assertEqual(metadata['connected_safety_equipment']['pressure_safety_valves'], [])

    def test_explicit_from_to_direction_is_relative_to_equipment(self):
        row = {**self.payload['items'][0], 'pid_no': 'PID-SYN-001'}
        inlet = build_equipment_metadata(row, 'FROM P-202 TO V-101')
        outlet = build_equipment_metadata(row, 'FROM V-101 TO P-202')
        self.assertEqual(inlet['relationships']['connected_equipment'][0]['direction'], 'inlet')
        self.assertEqual(outlet['relationships']['connected_equipment'][0]['direction'], 'outlet')

    def test_large_text_fallback_keeps_extraction_running(self):
        from .equipment_metadata import enrich_equipment_metadata
        row = {**self.payload['items'][0], 'pid_no': 'PID-SYN-001'}
        oversized = '\n'.join(
            f'V-101 CONNECTED TO SDV-{index:04d} AND PSV-{index:04d}'
            for index in range(1, 2000)
        )
        items = [row]
        enrich_equipment_metadata(items, oversized, {'extraction': {'equipment_metadata_ai_enabled': False}})
        self.assertTrue(items[0]['metadata']['relationships']['shutdown_valves'])
        self.assertLessEqual(
            len(items[0]['metadata']['relationships']['shutdown_valves']),
            75,
        )

    def test_model_relationships_require_verbatim_tag_linked_evidence(self):
        text = 'V-101 CONNECTED TO SDV-8003\nP-202 CONNECTED TO SDV-8004'
        proposed = normalise_equipment_metadata({'relationships': {
            'shutdown_valves': [
                {'tag': tag, 'drawing_no': 'PID-SYN-001', 'evidence': evidence, 'confidence': '70'}
                for tag, evidence in (
                    ('SDV-8003', 'V-101 CONNECTED TO SDV-8003'),
                    ('SDV-8004', 'P-202 CONNECTED TO SDV-8004'),
                    ('SDV-9999', 'V-101 CONNECTED TO SDV-9999'),
                )
            ],
        }})
        accepted = _source_supported_enrichment(proposed, text, 'V-101', 'PID-SYN-001')
        self.assertEqual([edge['tag'] for edge in accepted['relationships']['shutdown_valves']], ['SDV-8003'])

    def test_unsupported_ai_links_are_reported_but_not_saved(self):
        text = 'V-101 CONNECTED TO SDV-8003'
        items = [{'tag': 'V-101', 'pid_no': 'PID-SYN-001'}]
        reply = '{"relationships":{"shutdown_valves":[{"tag":"SDV-9999","drawing_no":"PID-SYN-001","evidence":"V-101 CONNECTED TO SDV-9999","confidence":70}]}}'
        with patch('apps.pid_analysis.multi_model_service.MultiModelAIService') as provider:
            provider.return_value.chat_completion.return_value = reply
            enrich_equipment_metadata(items, text, {'extraction': {'equipment_metadata_ai_enabled': True}})
        self.assertEqual(
            [link['tag'] for link in items[0]['metadata']['relationships']['shutdown_valves']],
            ['SDV-8003'],
        )
        self.assertIn('Some AI links lacked source evidence; verify the drawing.',
                      items[0]['metadata']['validation_findings']['warnings'])

    def test_duplicate_equipment_merges_evidence_without_cross_drawing_inference(self):
        from .equipment_analysis_views import _dedup_equipment_by_tag
        first = {
            'tag': 'V-101', 'pid_no': 'PID-1001',
            'source_locator': {'filename': 'first.pdf', 'page': 1},
        }
        second = {
            'tag': 'V-101', 'pid_no': 'PID-2002',
            'source_locator': {'filename': 'second.pdf', 'page': 2},
        }
        first['metadata'] = build_equipment_metadata(first, 'V-101 CONNECTED TO SDV-8003')
        second['metadata'] = build_equipment_metadata(second, 'V-101 CONNECTED TO PSV-9004')
        rows = _dedup_equipment_by_tag([first, second])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            {doc['drawing_no'] for doc in rows[0]['equipment_master']['source_documents']},
            {'PID-1001', 'PID-2002'},
        )
        links = rows[0]['equipment_master']['relationships']
        self.assertEqual(links['shutdown_valves'][0]['drawing_no'], 'PID-1001')
        self.assertEqual(links['safety_valves'][0]['drawing_no'], 'PID-2002')

    def test_equipment_master_keeps_each_parallel_unit(self):
        from .equipment_analysis_views import _dedup_equipment_by_tag
        rows = []
        for tag in ('P-851A', 'P-851B'):
            item = {'tag': tag, 'pid_no': 'PID-1001'}
            item['metadata'] = build_equipment_metadata(item)
            item['equipment_master'] = item['metadata']
            rows.append(item)
        self.assertEqual([row['tag'] for row in _dedup_equipment_by_tag(rows)],
                         ['P-851A', 'P-851B'])

    def test_master_is_persisted_and_invalid_relationship_rejected(self):
        metadata = build_equipment_metadata({
            **self.payload['items'][0], 'pid_no': 'PID-SYN-001',
            'source_locator': {'filename': 'synthetic-pid.pdf', 'page': 1},
        }, document_text='V-101 CONNECTED TO SDV-8003')
        response = self.client.post(self.import_url, {
            **self.payload, 'items': [{**self.payload['items'][0], 'metadata': metadata}],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        master = response.data['items'][0]['equipment_master']
        self.assertEqual(master['relationships']['shutdown_valves'][0]['tag'], 'SDV-8003')
        self.assertEqual(self.client.get(self.current_url).data['items'][0]['equipment_master'], master)

        rejected = self.client.post(self.import_url, {
            **self.payload, 'source_upload_id': 'EQ-BAD-002',
            'items': [{**self.payload['items'][0], 'metadata': {
                'relationships': {'alarms': [{'tag': 'PAH-1', 'drawing_no': 'PID-SYN-001'}]},
            }}],
        }, format='json')
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(EquipmentRevision.objects.count(), 1)

    def test_multi_page_extraction_scopes_relationships_to_their_source_page(self):
        from .tasks import _process_pid_pages

        config = {
            'extraction': {
                'equipment_metadata_ai_enabled': False,
                'ai_gap_fill_enabled': False,
                'merge_sibling_unit_variants': False,
            },
            'designation_codes': {}, 'tag_prefix_type_map': {},
        }
        with (
            patch('apps.pid_analysis.tasks._validate_pdf_pages', return_value=(2, b'%PDF-1.4')),
            patch('apps.pid_analysis.equipment_analysis_views._extract_equipment_register_rows', return_value=None),
            patch('apps.pid_analysis.equipment_analysis_views._extract_text_from_pdf', side_effect=[
                'V-101 CONNECTED TO SDV-8003',
                'P-202 CONNECTED TO PSV-9004',
            ]),
            patch('apps.pid_analysis.equipment_analysis_views._extract_titleblock_dwg_no',
                  side_effect=['PID-1001', 'PID-2002']),
            patch('apps.pid_analysis.equipment_analysis_views._extract_equipment_items',
                  side_effect=[[{'tag': 'V-101', 'drawing_ref': 'PID-1001'}],
                               [{'tag': 'P-202', 'drawing_ref': 'PID-2002'}]]),
            patch('apps.pid_analysis.equipment_analysis_views._ai_gap_fill_pid_items',
                  side_effect=lambda items, text, cfg: items),
            patch('apps.pid_analysis.equipment_analysis_views._extract_titleblock_revision',
                  return_value='A'),
        ):
            rows, _, mode, _ = _process_pid_pages(b'%PDF-1.4', 'synthetic.pdf', config)

        self.assertEqual(mode, 'pid_drawing')
        self.assertEqual(rows[0]['source_locator']['page'], 1)
        self.assertEqual(rows[0]['equipment_master']['relationships']['shutdown_valves'][0]['tag'], 'SDV-8003')
        self.assertEqual(rows[0]['equipment_master']['relationships']['safety_valves'], [])
        self.assertEqual(rows[1]['source_locator']['page'], 2)
        self.assertEqual(rows[1]['equipment_master']['relationships']['safety_valves'][0]['drawing_no'], 'PID-2002')
        self.assertEqual(rows[1]['equipment_master']['relationships']['shutdown_valves'], [])

    def test_new_extraction_preserves_approved_revision(self):
        first = self.import_register().data
        approved = EquipmentRevision.objects.get(pk=first['revision']['id'])
        approved.status = EquipmentRevision.Status.APPROVED
        approved.is_immutable = True
        approved.save(update_fields=['status', 'is_immutable'])

        response = self.client.post(self.import_url, {
            **self.payload,
            'source_upload_id': 'EQ-SYNTHETIC-002',
            'source_files': ['synthetic-pid-rev-b.pdf'],
            'items': [{**self.payload['items'][0], 'design_pressure_max': '190'}],
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['revision']['number'], 2)
        self.assertEqual(response.data['revision']['status'], EquipmentRevision.Status.DRAFT)
        approved.refresh_from_db()
        self.assertEqual(approved.status, EquipmentRevision.Status.APPROVED)
        self.assertTrue(approved.is_immutable)
        self.assertEqual(approved.items.get().design_pressure_max, '180')

        old_retry = self.client.post(self.import_url, self.payload, format='json')
        self.assertEqual(old_retry.status_code, 409)
        self.assertEqual(old_retry.data['code'], 'source_upload_already_imported')
        register = EquipmentRegister.objects.get()
        self.assertEqual(register.current_revision.number, 2)


class EquipmentMasterHarvestVisionTests(SimpleTestCase):
    """Deterministic harvesters, vision anti-hallucination, and batch resolution."""

    def _vision_item(self, tag='V-101', drawing='PID-SYN-001', filename='synthetic-pid.pdf', page=1):
        item = {
            'tag': tag, 'pid_no': drawing,
            'source_locator': {'filename': filename, 'drawing_no': drawing, 'page': page},
        }
        item['metadata'] = build_equipment_metadata(item, '')
        return item

    def test_note_harvest_links_citation_definition_and_alarm(self):
        metadata = build_equipment_metadata({
            'tag': 'V-101', 'pid_no': 'PID-SYN-001',
            'source_locator': {'filename': 'synthetic-pid.pdf', 'page': 3},
        }, document_text=(
            'V-101 SEE NOTE 3\n'
            'NOTE 3: PAHH-8002 ALARM SETPOINT 16 BARG VERIFY WITH OPERATIONS\n'
            'NOTE 4: PAINTING PER PROJECT SPEC'
        ))
        notes = metadata['relationships']['engineering_notes']
        self.assertEqual(len(notes), 1)
        self.assertIn('PAHH-8002', notes[0]['description'])
        self.assertEqual(notes[0]['evidence'], 'V-101 SEE NOTE 3')
        self.assertEqual(notes[0]['page'], 3)
        self.assertEqual(notes[0]['filename'], 'synthetic-pid.pdf')
        alarms = metadata['relationships']['alarms']
        self.assertEqual([entry['tag'] for entry in alarms], ['PAHH-8002'])

    def test_line_list_harvest_adds_lines_direction_and_counterpart(self):
        text = (
            'LINE LIST\n'
            '4"-PL-101 SOUR GAS FROM V-101 TO P-202\n'
            '4"-PL-102 SOUR GAS FROM V-101 TO E-301\n'
            '6"-PL-103 SOUR GAS FROM P-202 TO V-102\n'
            '6"-PL-104 SOUR GAS FROM V-102 TO E-301\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-101', 'pid_no': 'PID-SYN-001'}, text, None)
        line_entries = {entry['tag']: entry for entry in relationships['process_lines']}
        self.assertIn('4"-PL-101', line_entries)
        self.assertEqual(line_entries['4"-PL-101']['direction'], 'outlet')
        self.assertEqual(
            {entry['tag'] for entry in relationships['connected_equipment']},
            {'P-202', 'E-301'},
        )

    def test_line_list_harvest_requires_minimum_rows(self):
        text = (
            '4"-PL-101 SOUR GAS FROM V-101 TO P-202\n'
            '4"-PL-102 SOUR GAS FROM V-101 TO E-301\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-101', 'pid_no': 'PID-SYN-001'}, text, None)
        self.assertEqual(relationships['process_lines'], [])
        self.assertEqual(relationships['connected_equipment'], [])

    def test_instrument_schedule_harvest_classifies_schedule_rows(self):
        text = (
            'INSTRUMENT LIST\n'
            'TAG SERVICE LOOP EQUIPMENT\n'
            'PT-8003 SOUR GAS V-101\n'
            'PI-8003 SOUR GAS V-101\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-101', 'pid_no': 'PID-SYN-001'}, text, None)
        instruments = relationships['instruments']
        self.assertEqual([entry['tag'] for entry in instruments], ['PT-8003', 'PI-8003'])
        self.assertEqual(instruments[0]['evidence'], 'PT-8003 SOUR GAS V-101')

    def test_databox_attributes_harvest_internals_and_trim(self):
        metadata = build_equipment_metadata({
            'tag': 'V-101', 'pid_no': 'PID-SYN-001',
        }, document_text='V-101 INTERNALS: 4x SIEVE TRAYS TRIM: SS316L')
        attributes = metadata['attributes']
        self.assertIn('SIEVE TRAYS', attributes['internals']['value'])
        self.assertEqual(attributes['trim']['value'], 'SS316L')
        internals = metadata['relationships']['internals']
        self.assertTrue(any('SIEVE TRAYS' in entry['description'] for entry in internals))

    def test_harvesters_respect_disabled_config(self):
        config = {'extraction': {
            'equipment_notes_enabled': False,
            'equipment_notes_blocks_enabled': False,
            'equipment_line_list_enabled': False,
            'equipment_schedule_sections_enabled': False,
            'equipment_connector_sections_enabled': False,
            'equipment_databox_attributes_enabled': False,
        }}
        text = (
            'V-101 SEE NOTE 3\n'
            'NOTE 3: PAHH-8002 ALARM SETPOINT\n'
            'PT-8003 SOUR GAS V-101\n'
            'V-101 INTERNALS: 4x SIEVE TRAYS\n'
            'CONNECTOR SCHEDULE\n'
            'V-101 UPSTREAM V-201 PJ6-EXD-MRI-0023\n'
        )
        relationships, attributes = _harvest_relationships(
            {'tag': 'V-101', 'pid_no': 'PID-SYN-001'}, text, config)
        self.assertEqual(
            {group: entries for group, entries in relationships.items() if entries}, {})
        self.assertEqual(attributes, {})

    def test_isa_tag_families_and_train_suffixes_classify(self):
        text = (
            'INSTRUMENT LIST\n'
            'TAG SERVICE EQUIPMENT\n'
            'PT-8001A-TF SOUR GAS V-805-TF\n'
            'PI-8002L-TF SOUR GAS V-805-TF\n'
            'DPAH-8001-TF HIGH DP V-805-TF\n'
            'PG-8001-TF LOCAL GAUGE V-805-TF\n'
            'XS-8001-TF PIG DETECT V-805-TF\n'
            'MOV-8001-TF ACTUATED V-805-TF\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, None)
        self.assertEqual(
            [entry['tag'] for entry in relationships['instruments']],
            ['PT-8001A-TF', 'PI-8002L-TF', 'DPAH-8001-TF', 'PG-8001-TF', 'XS-8001-TF'],
        )
        self.assertEqual(
            [entry['tag'] for entry in relationships['alarms']], ['DPAH-8001-TF'])
        self.assertEqual(
            [entry['tag'] for entry in relationships['control_valves']], ['MOV-8001-TF'])

    def test_psv_schedule_row_links_safety_valve_with_details(self):
        text = (
            'PSV SCHEDULE\n'
            'TAG SET PRESSURE CASE EQUIPMENT\n'
            'PSV-8001-TF 600 PSIG FIRE CASE V-805-TF\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, None)
        safety = relationships['safety_valves']
        self.assertEqual([entry['tag'] for entry in safety], ['PSV-8001-TF'])
        self.assertIn('600 PSIG', safety[0]['description'])

    def test_line_list_single_equipment_rows_link_lines(self):
        text = (
            'LINE LIST\n'
            '20"-PL-DC3N-8106 WELL FLUID TO V-805-TF\n'
            '14"-PL-DC3N-8107 WELL FLUID FROM V-805-TF\n'
            '4"-PL-DC3N-8108 WELL FLUID V-805-TF\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, None)
        lines = {entry['tag']: entry for entry in relationships['process_lines']}
        self.assertIn('20"-PL-DC3N-8106', lines)
        self.assertEqual(lines['20"-PL-DC3N-8106']['direction'], 'inlet')
        self.assertEqual(lines['14"-PL-DC3N-8107']['direction'], 'outlet')

    def test_equipment_notes_block_links_alarms_permissives_and_mechanical(self):
        text = (
            'V-805-TF NOTES\n'
            'DPAH-8001 HIGH DIFFERENTIAL PRESSURE ALARM\n'
            'PI-8002L SAFETY CRITICAL ALARM\n'
            'SDV-8001-TF CLOSURE DCS ALARM\n'
            'HIGH DIFFERENTIAL PRESSURE < 100 PSI PERMISSIVE TO OPEN SDV-8002-TF\n'
            'WAREHOUSE SPARE PSV-8008-TF\n'
            'QUICK OPENING CLOSURE DOOR\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, None)
        self.assertIn('DPAH-8001', [entry['tag'] for entry in relationships['alarms']])
        self.assertIn('PI-8002L', [entry['tag'] for entry in relationships['instruments']])
        self.assertIn(
            'SDV-8001-TF', [entry['tag'] for entry in relationships['shutdown_valves']])
        self.assertIn(
            'SDV-8002-TF', [entry['tag'] for entry in relationships['shutdown_valves']])
        self.assertIn(
            'PSV-8008-TF', [entry['tag'] for entry in relationships['safety_valves']])
        self.assertTrue(any(
            'CLOSURE DOOR' in entry['description']
            for entry in relationships['internals']))

    def test_connector_section_links_cross_drawing_references(self):
        text = (
            'CONNECTOR SCHEDULE\n'
            'V-805-TF UPSTREAM SOURCE V-621-CF PJ6-EXD-CFP-BQDA-0006\n'
            'DOWNSTREAM DESTINATION V-803-TF PJ6-EXD-MRI-BQDA-0023\n'
            'DRAIN DESTINATION PJ6-EXD-MRI-BQDA-0024\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PJ6-EXD-MRI-BQDA-0022'}, text, None)
        cross = [entry['tag'] for entry in relationships['cross_pid_references']]
        self.assertIn('PJ6-EXD-CFP-BQDA-0006', cross)
        self.assertIn('PJ6-EXD-MRI-BQDA-0023', cross)
        self.assertIn('PJ6-EXD-MRI-BQDA-0024', cross)
        connected = [entry['tag'] for entry in relationships['connected_equipment']]
        self.assertIn('V-621-CF', connected)
        self.assertIn('V-803-TF', connected)

    def test_relationship_tag_patterns_config_override(self):
        config = {'extraction': {
            'relationship_tag_patterns': {'instruments': r'\b(?:ZI)-?\d{3,6}\b'},
        }}
        text = 'INSTRUMENT LIST\nZI-700 V-805-TF\nPT-8001 V-805-TF'
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, config)
        self.assertEqual(
            [entry['tag'] for entry in relationships['instruments']], ['ZI-700'])

    def test_relationship_budget_trims_overflow_without_wiping_links(self):
        text = 'LINE LIST\n' + '\n'.join(
            f'4"-PL-DC3N-{8100 + index} WELL FLUID TO V-805-TF' for index in range(50)
        )
        metadata = build_equipment_metadata({'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text)
        relationships = metadata['relationships']
        total = sum(len(entries) for entries in relationships.values())
        self.assertEqual(total, 45)
        self.assertEqual(len(relationships['process_lines']), 45)
        self.assertIn(
            'Relationship evidence exceeded metadata bounds; some proposed '
            'links were trimmed. Review the source drawing.',
            metadata['validation_findings']['warnings'],
        )

    def test_ocr_tolerant_tag_matching_associates_schedules(self):
        text = (
            'INSTRUMENT LIST\n'
            'PT-8001A-TF SOUR GAS V-805\n'
            'PG-8001-TF LOCAL GAUGE V–805–TF\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, None)
        tags = [entry['tag'] for entry in relationships['instruments']]
        self.assertIn('PT-8001A-TF', tags)
        self.assertIn('PG-8001-TF', tags)

    def test_schedule_section_survives_blank_lines(self):
        text = (
            'PSV SCHEDULE\n'
            'PSV-8001-TF 600 PSIG FIRE CASE V-805-TF\n'
            '\n'
            '\n'
            'PSV-8002-TF 550 PSIG V-805-TF\n'
        )
        relationships, _ = _harvest_relationships(
            {'tag': 'V-805-TF', 'pid_no': 'PID-1'}, text, None)
        self.assertEqual(
            [entry['tag'] for entry in relationships['safety_valves']],
            ['PSV-8001-TF', 'PSV-8002-TF'],
        )

    def test_relationship_provenance_fields_are_validated(self):
        def envelope(**entry):
            return {'relationships': {'instruments': [{
                'tag': 'PT-8003', 'drawing_no': 'PID-SYN-001',
                'evidence': 'V-101 PT-8003', **entry,
            }]}}

        with self.assertRaises(ValueError):
            normalise_equipment_metadata(envelope(source='x'))
        with self.assertRaises(ValueError):
            normalise_equipment_metadata(envelope(bbox=[1, 2, 3]))
        with self.assertRaises(ValueError):
            normalise_equipment_metadata(envelope(resolution='bogus'))
        accepted = normalise_equipment_metadata(envelope(
            source='vision', bbox=[10, 20, 30, 40], resolution='resolved_in_batch',
            resolved_drawing_no='PID-2002', resolved_filename='other.pdf', resolved_page=2,
        ))
        entry = accepted['relationships']['instruments'][0]
        self.assertEqual(entry['source'], 'vision')
        self.assertEqual(entry['bbox'], [10, 20, 30, 40])
        self.assertEqual(entry['resolution'], 'resolved_in_batch')
        self.assertEqual(entry['resolved_drawing_no'], 'PID-2002')
        self.assertEqual(entry['resolved_filename'], 'other.pdf')
        self.assertEqual(entry['resolved_page'], 2)

    def test_equipment_master_view_reports_coverage_and_provenance(self):
        metadata = normalise_equipment_metadata({'relationships': {
            'instruments': [{
                'tag': 'PT-8003', 'drawing_no': 'PID-SYN-001',
                'evidence': 'V-101 PT-8003', 'source': 'vision',
            }],
        }})
        view = equipment_master_view(metadata)
        coverage = view['extraction_coverage']
        self.assertEqual(coverage['groups_found'], 1)
        self.assertEqual(coverage['groups_total'], 12)
        self.assertEqual(coverage['completeness'], round(1 / 12, 4))
        self.assertTrue(coverage['groups']['instruments']['found'])
        self.assertEqual(coverage['groups']['instruments']['sources'], ['vision'])
        self.assertFalse(coverage['groups']['alarms']['found'])
        self.assertEqual(view['provenance'], {'text': 0, 'vision': 1})

    def test_render_page_images_never_raises_on_invalid_pdf(self):
        self.assertEqual(render_page_images(b'not-a-pdf', {'extraction': {}}), [])

    def test_vision_enrichment_merges_only_text_anchored_links(self):
        items = [self._vision_item()]
        page_text = 'V-101 CONNECTED TO SDV-8003'
        reply = json.dumps({'V-101': {'relationships': {
            'shutdown_valves': [
                {'tag': 'SDV-8003', 'description': '', 'direction': '',
                 'evidence': 'SDV-8003 label at suction nozzle', 'confidence': 88},
                {'tag': 'XV-9999', 'description': '', 'direction': '',
                 'evidence': 'XV-9999 near south nozzle', 'confidence': 92},
            ],
        }, 'attributes': {}}})
        with patch('apps.pid_analysis.equipment_vision.MultiModelAIService') as provider:
            provider.return_value.vision_analysis.return_value = reply
            vision_enrich_equipment(items, 'aW1hZ2U=', page_text,
                                    {'extraction': {'equipment_vision_enabled': True}})
        master = items[0]['equipment_master']
        shutdown = master['relationships']['shutdown_valves']
        self.assertNotIn('XV-9999', [entry['tag'] for entry in shutdown])
        vision_entries = [entry for entry in shutdown if entry.get('source') == 'vision']
        self.assertEqual([entry['tag'] for entry in vision_entries], ['SDV-8003'])
        self.assertEqual(vision_entries[0]['confidence'], '50')  # not verbatim in page text
        self.assertEqual(vision_entries[0]['drawing_no'], 'PID-SYN-001')
        self.assertEqual(vision_entries[0]['filename'], 'synthetic-pid.pdf')
        self.assertEqual(vision_entries[0]['page'], 1)
        self.assertIn('Some vision links lacked source support; verify the drawing.',
                      master['validation_findings']['warnings'])

    def test_vision_provider_failure_keeps_deterministic_metadata(self):
        items = [self._vision_item()]
        with patch('apps.pid_analysis.equipment_vision.MultiModelAIService') as provider:
            provider.return_value.vision_analysis.side_effect = Exception('quota exceeded')
            vision_enrich_equipment(items, 'aW1hZ2U=', 'V-101 TEST SEPARATOR',
                                    {'extraction': {'equipment_vision_enabled': True}})
        self.assertEqual(items[0]['metadata']['relationships'], {})
        self.assertIn('Vision enrichment failed; verify the source drawing.',
                      items[0]['metadata']['validation_findings']['warnings'])

    def test_vision_unavailable_provider_skips_without_raise(self):
        items = [self._vision_item()]
        with patch('apps.pid_analysis.equipment_vision.MultiModelAIService',
                   side_effect=Exception('no provider key')):
            vision_enrich_equipment(items, 'aW1hZ2U=', 'V-101 TEST SEPARATOR',
                                    {'extraction': {'equipment_vision_enabled': True}})
        self.assertEqual(items[0]['metadata']['relationships'], {})
        self.assertIn('Vision enrichment was unavailable; verify the source drawing.',
                      items[0]['metadata']['validation_findings']['warnings'])

    def test_batch_resolution_links_drawings_in_same_upload(self):
        first = {
            'tag': 'V-101', 'pid_no': 'PID-1001',
            'source_locator': {'filename': 'first.pdf', 'drawing_no': 'PID-1001', 'page': 1},
        }
        second = {
            'tag': 'P-202', 'pid_no': 'PID-2002',
            'source_locator': {'filename': 'second.pdf', 'drawing_no': 'PID-2002', 'page': 2},
        }
        first['metadata'] = normalise_equipment_metadata({'relationships': {
            'cross_pid_references': [
                {'tag': 'PID-2002', 'drawing_no': 'PID-1001',
                 'evidence': 'V-101 CONTINUED ON P&ID PID-2002'}],
            'connected_equipment': [
                {'tag': 'P-202', 'drawing_no': 'PID-1001',
                 'evidence': 'V-101 CONNECTED TO P-202'}],
        }})
        second['metadata'] = normalise_equipment_metadata({})
        rows = resolve_batch_cross_references([first, second])
        cross = rows[0]['metadata']['relationships']['cross_pid_references'][0]
        self.assertEqual(cross['resolution'], 'resolved_in_batch')
        self.assertEqual(cross['resolved_drawing_no'], 'PID-2002')
        self.assertEqual(cross['resolved_filename'], 'second.pdf')
        self.assertEqual(cross['resolved_page'], 2)
        connected = rows[0]['metadata']['relationships']['connected_equipment'][0]
        self.assertEqual(connected['resolution'], 'resolved_in_batch')
        self.assertEqual(connected['resolved_drawing_no'], 'PID-2002')
        master_cross = rows[0]['equipment_master']['relationships']['cross_pid_references'][0]
        self.assertEqual(master_cross['resolution'], 'resolved_in_batch')

    def test_batch_resolution_marks_unmatched_cross_reference_unresolved(self):
        item = {
            'tag': 'V-101', 'pid_no': 'PID-1001',
            'source_locator': {'filename': 'first.pdf', 'drawing_no': 'PID-1001', 'page': 1},
        }
        item['metadata'] = normalise_equipment_metadata({'relationships': {
            'cross_pid_references': [
                {'tag': 'PID-9999', 'drawing_no': 'PID-1001',
                 'evidence': 'V-101 CONTINUED ON P&ID PID-9999'}],
        }})
        other = {
            'tag': 'P-202', 'pid_no': 'PID-2002',
            'source_locator': {'filename': 'second.pdf', 'drawing_no': 'PID-2002', 'page': 1},
        }
        other['metadata'] = normalise_equipment_metadata({})
        rows = resolve_batch_cross_references([item, other])
        cross = rows[0]['metadata']['relationships']['cross_pid_references'][0]
        self.assertEqual(cross['resolution'], 'unresolved')
        self.assertEqual(cross['resolved_drawing_no'], '')

    def test_batch_resolution_leaves_single_drawing_batch_untouched(self):
        first = {
            'tag': 'V-101', 'pid_no': 'PID-1001',
            'source_locator': {'filename': 'shared.pdf', 'drawing_no': 'PID-1001', 'page': 1},
        }
        second = {
            'tag': 'P-202', 'pid_no': 'PID-1001',
            'source_locator': {'filename': 'shared.pdf', 'drawing_no': 'PID-1001', 'page': 1},
        }
        first['metadata'] = normalise_equipment_metadata({'relationships': {
            'cross_pid_references': [
                {'tag': 'PID-2002', 'drawing_no': 'PID-1001',
                 'evidence': 'V-101 CONTINUED ON P&ID PID-2002'}],
            'connected_equipment': [
                {'tag': 'P-202', 'drawing_no': 'PID-1001',
                 'evidence': 'V-101 CONNECTED TO P-202'}],
        }})
        second['metadata'] = normalise_equipment_metadata({})
        rows = resolve_batch_cross_references([first, second])
        relationships = rows[0]['metadata']['relationships']
        self.assertEqual(relationships['cross_pid_references'][0]['resolution'], '')
        self.assertEqual(relationships['connected_equipment'][0]['resolution'], '')
        self.assertEqual(relationships['connected_equipment'][0]['resolved_drawing_no'], '')
