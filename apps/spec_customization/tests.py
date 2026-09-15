"""Regression tests for the safety boundary around Spec Customization."""
from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
    PipingClassComponent,
    WorkbookCellOverride,
)
from .workbook_storage_service import batch_save_cells, bump_workbook_revision, get_workbook_revision
from .services.extraction_service import PaperSpecExtractionService
from .services.workbook_validation import _validate_sheet
from .services.workbook_chatbot import (
    _is_repair_instruction,
    _plan_safe_repairs_for_workbook,
    _plan_updates_for_workbook,
    _uploaded_document_context,
)


class JobAuthorizationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='spec-owner', email='spec-owner@example.test', password='safe-password'
        )
        self.other_user = user_model.objects.create_user(
            username='spec-other', email='spec-other@example.test', password='safe-password'
        )
        document = PaperSpecDocument.objects.create(
            file=SimpleUploadedFile('source.pdf', b'%PDF-1.4 test'),
            original_filename='source.pdf',
            file_size_bytes=13,
            sha256_hash='a' * 64,
            uploaded_by=self.owner,
        )
        self.job = PaperSpecExtractionJob.objects.create(document=document, created_by=self.owner)
        self.client = APIClient()

    def test_other_authenticated_user_cannot_read_job(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/')

        self.assertEqual(response.status_code, 403)

    def test_creator_can_read_job(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/')

        self.assertEqual(response.status_code, 200)


class WorkbookRevisionTests(TestCase):
    def test_revision_changes_after_a_workbook_write(self):
        cache.clear()
        job_id = '00000000-0000-0000-0000-000000000001'

        initial = get_workbook_revision(job_id, 'spec')
        updated = bump_workbook_revision(job_id, 'spec')

        self.assertGreater(updated, initial)
        self.assertEqual(get_workbook_revision(job_id, 'spec'), updated)


class ExtractionCompletenessTests(SimpleTestCase):
    def test_header_only_text_result_is_not_accepted_as_an_extraction(self):
        service = PaperSpecExtractionService()

        self.assertFalse(service._text_layer_result_is_complete([
            {"class_code": "A", "components": []},
        ]))

    def test_text_result_with_a_component_can_use_the_fast_path(self):
        service = PaperSpecExtractionService()

        self.assertTrue(service._text_layer_result_is_complete([
            {"class_code": "A", "components": [{"component_type": "valve"}]},
        ]))


class WorkbookChatbotPlanningTests(SimpleTestCase):
    def test_natural_rectify_request_is_recognized(self):
        self.assertTrue(_is_repair_instruction('can you rectify those issue and align the data'))

    @patch('apps.spec_customization.services.workbook_chatbot.build_preview')
    def test_repair_request_plans_safe_normalizations(self, build_preview):
        build_preview.return_value = {
            'sheets': [{
                'name': 'PipingComponentData',
                'headers': ['PressureRating', 'FlangeFacing', 'EndConnection', 'SizeFrom', 'SizeTo'],
                'rows': [{
                    'row_key': 'comp:component-id:PipingComponentData:0',
                    'source': {'class_id': 'class-id', 'class_code': 'A1', 'component_id': 'component-id'},
                    'cells': {
                        'PressureRating': 'CL150',
                        'FlangeFacing': 'Raised Face',
                        'EndConnection': 'Butt Weld',
                        'SizeFrom': '12',
                        'SizeTo': '2',
                    },
                }],
            }],
        }

        result = _plan_safe_repairs_for_workbook(object(), 'spec')

        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'repair')
        self.assertEqual(
            {(update['column_name'], update['value']) for update in result['planned_updates']},
            {
                ('PressureRating', 'CLASS 150'),
                ('FlangeFacing', 'RF'),
                ('EndConnection', 'BW'),
                ('SizeFrom', '2'),
                ('SizeTo', '12'),
            },
        )

    def test_document_context_includes_linkable_record_metadata(self):
        document = SimpleNamespace(
            id='document-id',
            file=SimpleNamespace(name='spec_customization/paper_specs/source.pdf', url='/media/source.pdf'),
            original_filename='source.pdf',
            project_id='project-id',
            title='Piping specification',
            document_number='SPEC-001',
            file_size_bytes=123,
            total_pages=7,
            sha256_hash='a' * 64,
        )

        context = _uploaded_document_context(SimpleNamespace(document=document))

        self.assertEqual(context['document_id'], 'document-id')
        self.assertEqual(context['file_url'], '/media/source.pdf')
        self.assertEqual(context['project_id'], 'project-id')
        self.assertEqual(context['document_number'], 'SPEC-001')

    @patch('apps.spec_customization.services.workbook_chatbot._retrieval_index')
    def test_matched_update_keeps_previous_value_and_source(self, retrieval_index):
        retrieval_index.return_value = {
            'headers': ['Description', 'MaterialGrade'],
            'rows': [{
                'sheet_name': 'PipingMaterialsClassData',
                'row_key': 'comp:component-id:PipingMaterialsClassData:0',
                'source': {
                    'class_id': 'class-id',
                    'class_code': 'A1',
                    'component_id': 'component-id',
                },
                'cells': {
                    'Description': 'Carbon steel pipe',
                    'MaterialGrade': 'ASTM A53',
                },
                'blob': 'PipingMaterialsClassData A1 Carbon steel pipe ASTM A53',
            }],
            '_cache_hit': False,
        }

        result = _plan_updates_for_workbook(
            object(),
            'spec',
            'set MaterialGrade to ASTM A106 Gr.B where Description contains pipe',
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['planned_count'], 1)
        self.assertEqual(result['planned_updates'][0]['previous_value'], 'ASTM A53')
        self.assertEqual(result['planned_updates'][0]['source'], {
            'class_id': 'class-id',
            'class_code': 'A1',
            'component_id': 'component-id',
        })


class WorkbookValidationTests(SimpleTestCase):
    def test_template_rows_are_not_treated_as_extracted_records(self):
        issues = []
        checked = _validate_sheet('spec', {
            'name': 'PipingCommodityFilter',
            'headers': ['CommodityCode', 'Description'],
            'rows': [{
                'row_key': 'tpl:PipingCommodityFilter:3',
                'source': {'class_id': None, 'class_code': None, 'component_id': None},
                'cells': {'CommodityCode': '', 'Description': ''},
            }],
        }, issues)

        self.assertEqual(checked, 0)
        self.assertEqual(issues, [])


class WorkbookOverrideProvenanceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='workbook-editor', email='workbook-editor@example.test', password='safe-password'
        )
        document = PaperSpecDocument.objects.create(
            file=SimpleUploadedFile('source.pdf', b'%PDF-1.4 test'),
            original_filename='source.pdf',
            file_size_bytes=13,
            sha256_hash='b' * 64,
            uploaded_by=self.user,
        )
        self.job = PaperSpecExtractionJob.objects.create(document=document, created_by=self.user)
        self.piping_class = PipingClass.objects.create(job=self.job, class_code='A1')
        self.component = PipingClassComponent.objects.create(
            piping_class=self.piping_class,
            component_type=PipingClassComponent.TYPE_PIPE,
        )

    def test_chatbot_provenance_is_saved_and_manual_edit_clears_it(self):
        approved_at = timezone.now()
        cell = {
            'workbook': 'spec',
            'sheet_name': 'PipingMaterialsClassData',
            'row_key': f'comp:{self.component.id}:PipingMaterialsClassData:0',
            'column_name': 'MaterialGrade',
            'value': 'ASTM A106 Gr.B',
            'source_class_id': self.piping_class.id,
            'source_component_id': self.component.id,
            'edit_origin': WorkbookCellOverride.EDIT_ORIGIN_CHATBOT,
            'evidence_pages': [12, 13],
            'auto_value_used': True,
            'approved_at': approved_at,
        }

        batch_save_cells(job=self.job, cells=[cell], user=self.user)
        override = WorkbookCellOverride.objects.get(job=self.job)
        self.assertEqual(override.source_class_id, self.piping_class.id)
        self.assertEqual(override.source_component_id, self.component.id)
        self.assertEqual(override.edit_origin, WorkbookCellOverride.EDIT_ORIGIN_CHATBOT)
        self.assertEqual(override.evidence_pages, [12, 13])
        self.assertTrue(override.auto_value_used)
        self.assertEqual(override.approved_at, approved_at)

        batch_save_cells(job=self.job, cells=[{
            'workbook': cell['workbook'],
            'sheet_name': cell['sheet_name'],
            'row_key': cell['row_key'],
            'column_name': cell['column_name'],
            'value': 'Manual correction',
        }], user=self.user)
        override.refresh_from_db()
        self.assertIsNone(override.source_class_id)
        self.assertIsNone(override.source_component_id)
        self.assertEqual(override.edit_origin, WorkbookCellOverride.EDIT_ORIGIN_MANUAL)
        self.assertEqual(override.evidence_pages, [])
        self.assertFalse(override.auto_value_used)
        self.assertIsNone(override.approved_at)
