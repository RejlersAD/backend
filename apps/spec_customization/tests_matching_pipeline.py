"""Storage-independent matching and export-readiness regression coverage."""
from io import BytesIO
from unittest.mock import patch
from types import SimpleNamespace
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import InMemoryStorage
from django.db import ProgrammingError
from django.test import TestCase, override_settings
from openpyxl import Workbook
from rest_framework.test import APIClient
from .matching_models import MatchingWorkbookSet, MatchingRule, ComponentMatchingResult
from .models import PaperSpecDocument, PaperSpecExtractionJob, PipingClass
from .project_models import SpecProject
from .services.component_matcher import match_component, parse_match_xlsx


def xlsx(sheet, headers, rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class RemoteMemoryStorage(InMemoryStorage):
    def _relative_path(self, name):
        return name.lstrip('/')

    def path(self, name):
        raise NotImplementedError('No local path')


class MatchingPipelineTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='matching-owner', email='matching-owner@example.test')
        self.other = get_user_model().objects.create_user(username='matching-other', email='matching-other@example.test')
        self.project = SpecProject.objects.create(name='Test project', created_by=self.owner)
        self.storage = RemoteMemoryStorage()
        for name in ('match_file', 'spec_file', 'cat_file'):
            self.enterContext(patch.object(MatchingWorkbookSet._meta.get_field(name), 'storage', self.storage))
        self.reference = MatchingWorkbookSet.objects.create(
            project=self.project, uploaded_by=self.owner, is_active=True,
            match_file=ContentFile(xlsx('Mappings', ['PDF', 'B', 'C', 'Catalog'], [['GATE VALVE', '', '', 'Gate Valve']]), name='Match.xlsx'),
            cat_file=ContentFile(xlsx('GateValve', ['IndustryCommodityCode', 'Npd[1]', 'PressureRating[1]', 'MaterialGrade'], [
                ['WRONG-SIZE', 4, 150, 'A105'], ['VLV-2', 2, 150, 'A105'], ['WRONG-RATING', 2, 300, 'A105'],
            ]), name='CAT.xlsx'))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def match(self):
        return match_component('GATE VALVE', '2', '150#', 'A105', self.reference)

    def test_parser_reads_storage_without_local_path(self):
        with self.assertRaises(NotImplementedError):
            _ = self.reference.match_file.path
        self.assertEqual(parse_match_xlsx(self.reference.match_file, self.reference), (1, ''))
        self.assertEqual(MatchingRule.objects.get(workbook_set=self.reference).cat_sheet_name, 'GateValve')

    def test_unique_match_filters_noncontiguous_dataframe_indices(self):
        parse_match_xlsx(self.reference.match_file, self.reference)
        result = self.match()
        self.assertTrue(result['matched'])
        self.assertEqual(result['commodity_code'], 'VLV-2')
        self.assertEqual(ComponentMatchingResult.objects.count(), 1)

    def test_ambiguity_does_not_persist_first_candidate(self):
        parse_match_xlsx(self.reference.match_file, self.reference)
        self.reference.cat_file.save('ambiguous.xlsx', ContentFile(xlsx('GateValve',
            ['IndustryCommodityCode', 'Npd[1]', 'PressureRating[1]', 'MaterialGrade'],
            [['VLV-A', 2, 150, 'A105'], ['VLV-B', 2, 150, 'A105']])))
        result = self.match()
        self.assertFalse(result['matched'])
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(ComponentMatchingResult.objects.count(), 0)

    def test_invalid_mapping_columns_are_not_marked_successful(self):
        self.reference.match_file.save('invalid.xlsx', ContentFile(xlsx('Mappings', ['Only one column'], [['GATE VALVE']])))
        count, error = parse_match_xlsx(self.reference.match_file, self.reference)
        self.assertEqual(count, 0)
        self.assertIn('missing', error)

    def test_missing_material_column_cannot_satisfy_material_criteria(self):
        parse_match_xlsx(self.reference.match_file, self.reference)
        self.reference.cat_file.save('missing-material.xlsx', ContentFile(xlsx('GateValve',
            ['IndustryCommodityCode', 'Npd[1]', 'PressureRating[1]'], [['VLV-A', 2, 150]])))
        result = self.match()
        self.assertFalse(result['matched'])
        self.assertEqual(result['status'], 'invalid_reference')

    def test_unparsed_reference_cannot_match_through_endpoint(self):
        response = self.client.post('/api/v1/spec-customization/matching/match/', {
            'workbook_set_id': str(self.reference.id), 'pdf_component_name': 'GATE VALVE',
        })
        self.assertEqual(response.status_code, 409)

    def test_endpoints_support_storage_files_and_enforce_ownership(self):
        endpoint = f'/api/v1/spec-customization/matching/sets/{self.reference.id}/parse/'
        response = self.client.post(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(endpoint).status_code, 403)

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024 * 1024, FILE_UPLOAD_HANDLERS=['django.core.files.uploadhandler.MemoryFileUploadHandler'])
    def test_upload_parses_and_counts_sheets_without_local_paths(self):
        response = self.client.post('/api/v1/spec-customization/matching/upload/', {
            'project_id': str(self.project.pk),
            'match_file': SimpleUploadedFile('Match.xlsx', xlsx('Mappings', ['PDF', 'B', 'C', 'Catalog'], [['GATE VALVE', '', '', 'Gate Valve']])),
            'spec_file': SimpleUploadedFile('SPEC.xlsx', xlsx('MaterialsData', ['Material'], [['A105']])),
            'cat_file': SimpleUploadedFile('CAT.xlsx', xlsx('GateValve', ['IndustryCommodityCode'], [['VLV-2']])),
        }, format='multipart')
        self.assertEqual(response.status_code, 201)
        current = MatchingWorkbookSet.objects.get(project=self.project, is_active=True)
        self.assertTrue(current.is_parsed)
        self.assertEqual(current.rules_count, 1)
        self.assertEqual((current.spec_sheets_count, current.cat_sheets_count), (1, 1))

    def test_activation_leaves_one_active_set_for_project(self):
        replacement = MatchingWorkbookSet.objects.create(project=self.project, uploaded_by=self.owner)
        response = self.client.post(f'/api/v1/spec-customization/matching/sets/{replacement.id}/activate/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(MatchingWorkbookSet.objects.filter(project=self.project, is_active=True).values_list('id', flat=True)), [replacement.id])


class ExportReadinessTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='export-owner')
        document = PaperSpecDocument.objects.create(original_filename='test.pdf', uploaded_by=self.owner, sha256_hash='a' * 64)
        self.job = PaperSpecExtractionJob.objects.create(document=document, created_by=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_noncompleted_jobs_cannot_export(self):
        for status in ('queued', 'processing', 'failed', 'cancelled'):
            self.job.status = status
            self.job.save(update_fields=['status'])
            for kind in ('spec', 'cat'):
                with self.subTest(status=status, kind=kind):
                    response = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/export-{kind}/')
                    self.assertEqual(response.status_code, 409)

    def test_completed_job_retains_draft_export(self):
        self.job.status = 'completed'
        self.job.save(update_fields=['status'])
        with patch('apps.spec_customization.views.build_spec_workbook', return_value=BytesIO(b'test workbook')):
            response = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/export-spec/')
        self.assertEqual(response.status_code, 200)

    def test_completed_but_partial_job_is_reviewable_not_bulkload_exportable(self):
        self.job.status = 'completed'
        self.job.config_snapshot = {'extraction_outcome': {'failed_chunks': [{'page_from': 1, 'page_to': 2}]}}
        self.job.save(update_fields=['status', 'config_snapshot'])
        detail = self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['extraction_completeness']['state'], 'partial')
        for kind in ('spec', 'cat'):
            self.assertEqual(self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/export-{kind}/').status_code, 409)

    def test_worker_records_failed_chunk_ranges(self):
        from .tasks import extract_paper_spec
        service = self.enterContext(patch('apps.spec_customization.tasks.PaperSpecExtractionService')).return_value
        service.get_page_count.return_value = 2
        service.extract_appendix_a_summary.return_value = {
            'piping_classes': [], 'pages_scanned': [1], 'appendix_pages': [],
        }
        service.chunk_ranges.return_value = [(0, 0), (1, 1)]
        service.extract_chunk.side_effect = [RuntimeError('Synthetic chunk failure'), {'piping_classes': [], 'engine_used': 'text'}]
        service.merge_classes.return_value = []
        service.get_usage_and_cost.return_value = {
            'gemini_prompt_tokens': 0, 'gemini_completion_tokens': 0,
            'openai_prompt_tokens': 0, 'openai_completion_tokens': 0, 'cost_usd': 0,
        }
        self.enterContext(patch('apps.spec_customization.tasks._get_local_path', return_value=('synthetic.pdf', False)))
        self.enterContext(patch('apps.spec_customization.tasks._write_progress'))
        self.enterContext(patch('apps.spec_customization.tasks.validate_extracted_classes', return_value={
            'overall_status': 'warning', 'classes_validated': 0, 'warnings_total': 0,
            'template_comparison': {'accuracy_estimate': 0},
        }))
        self.enterContext(patch('apps.spec_customization.tasks.process_extracted_classes', return_value=([], {'duplicates_removed': 0})))
        extract_paper_spec.run(str(self.job.pk))
        self.job.refresh_from_db()
        self.assertEqual(self.job.config_snapshot['extraction_outcome']['failed_chunks'], [{'page_from': 1, 'page_to': 1}])
        self.assertIn('Partial extraction', self.job.error_message)
        service.log_zero_class_diagnostics.assert_called_once_with('synthetic.pdf', [1, 2])
        self.assertEqual(self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{self.job.id}/export-spec/').status_code, 409)

    def test_worker_persists_appendix_code_when_chunk_returns_no_classes(self):
        from .tasks import extract_paper_spec
        from .services.extraction_service import PaperSpecExtractionService
        service = self.enterContext(patch('apps.spec_customization.tasks.PaperSpecExtractionService')).return_value
        service.get_page_count.return_value = 30
        service.chunk_ranges.return_value = [(0, 29)]
        service.extract_appendix_a_summary.return_value = {
            'piping_classes': [PaperSpecExtractionService._appendix_class("011412-Z", 29, "appendix_regex")],
            'pages_scanned': [29], 'appendix_pages': [29],
        }
        service.extract_chunk.return_value = {'piping_classes': [], 'engine_used': 'none'}
        service.merge_classes.side_effect = PaperSpecExtractionService.merge_classes
        service.get_usage_and_cost.return_value = {
            'gemini_prompt_tokens': 0, 'gemini_completion_tokens': 0,
            'openai_prompt_tokens': 0, 'openai_completion_tokens': 0, 'cost_usd': 0,
        }
        self.enterContext(patch('apps.spec_customization.tasks._get_local_path', return_value=('synthetic.pdf', False)))
        self.enterContext(patch('apps.spec_customization.tasks._write_progress'))
        self.enterContext(patch('apps.spec_customization.tasks.validate_extracted_classes', return_value={
            'overall_status': 'warning', 'classes_validated': 1, 'warnings_total': 0,
            'template_comparison': {'accuracy_estimate': 0},
        }))

        result = extract_paper_spec.run(str(self.job.pk))

        self.assertEqual(result['classes'], 1)
        saved = PipingClass.objects.get(job=self.job)
        self.assertEqual(saved.class_code, "011412-Z")
        self.assertEqual(saved.source_pages, [29, 29])
        self.assertEqual(saved.components.count(), 0)
        service.log_zero_class_diagnostics.assert_not_called()


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class ProjectFileHistoryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='s3d-history-owner', email='s3d-history-owner@example.test')
        self.other = get_user_model().objects.create_user(username='s3d-history-other', email='s3d-history-other@example.test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.enterContext(patch.object(PaperSpecDocument._meta.get_field('file'), 'storage', RemoteMemoryStorage()))
        self.enterContext(patch('apps.spec_customization.views.normalize_to_pdf', side_effect=lambda src: SimpleNamespace(
            success=True, file=ContentFile(src.read(), name=src.name),
        )))
        self.enterContext(patch('apps.spec_customization.views._page_count', return_value=1))
        self.enterContext(patch('apps.spec_customization.views.extract_paper_spec.delay',
                                return_value=SimpleNamespace(id='queued-task')))

    def upload(self, filename, content):
        return self.client.post('/api/v1/spec-customization/paper-spec/upload/', {
            'file': SimpleUploadedFile(filename, content, content_type='application/pdf'),
            'auto_project': '1',
        }, format='multipart')

    def test_identical_bytes_reuse_project_and_new_bytes_get_new_folder(self):
        first = self.upload('piping-spec.pdf', b'%PDF-1.4 first')
        second = self.upload('renamed.pdf', b'%PDF-1.4 first')
        third = self.upload('piping-spec.pdf', b'%PDF-1.4 changed')
        self.assertEqual([first.status_code, second.status_code, third.status_code], [201, 201, 201])
        self.assertEqual(first.data['document']['project_id'], second.data['document']['project_id'])
        self.assertNotEqual(first.data['job']['id'], second.data['job']['id'])
        self.assertNotEqual(first.data['document']['project_id'], third.data['document']['project_id'])
        self.assertEqual(SpecProject.objects.filter(created_by=self.owner).count(), 2)
        self.assertEqual(first.data['job']['project_id'], first.data['document']['project_id'])
        self.assertIsNone(first.data['job']['saved_at'])

        self.client.force_authenticate(self.other)
        other = self.upload('shared.pdf', b'%PDF-1.4 first')
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.data['document']['project_id'], first.data['document']['project_id'])
        self.assertEqual(self.client.get(f"/api/v1/spec-customization/projects/{first.data['document']['project_id']}/jobs/").status_code, 403)

    def test_save_completed_job_is_explicit_idempotent_and_visible_in_history(self):
        first = self.upload('spec.pdf', b'%PDF-1.4 first')
        second = self.upload('spec-copy.pdf', b'%PDF-1.4 first')
        project_id = first.data['document']['project_id']
        first_job = PaperSpecExtractionJob.objects.get(pk=first.data['job']['id'])
        second_job = PaperSpecExtractionJob.objects.get(pk=second.data['job']['id'])
        url = f'/api/v1/spec-customization/paper-spec/jobs/{first_job.pk}/save/'
        self.assertEqual(self.client.post(url).status_code, 409)
        first_job.status = 'completed'
        first_job.save(update_fields=['status'])
        self.assertEqual(self.client.post(url).status_code, 409)
        PipingClass.objects.create(job=first_job, class_code='011412-Z')
        saved = self.client.post(url)
        self.assertEqual(saved.status_code, 200)
        self.assertIsNotNone(saved.data['saved_at'])
        self.assertEqual(self.client.post(url).data['saved_at'], saved.data['saved_at'])
        history = self.client.get(f'/api/v1/spec-customization/projects/{project_id}/jobs/')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data['current_saved_job_id'], str(first_job.pk))
        self.assertEqual(len(history.data['jobs']), 2)
        self.assertIsNone(next(j for j in history.data['jobs'] if j['id'] == str(second_job.pk))['saved_at'])
        self.assertEqual(next(j for j in history.data['jobs'] if j['id'] == str(first_job.pk))['saved_by_name'], self.owner.username)
        self.assertEqual(self.client.get(f'/api/v1/spec-customization/paper-spec/jobs/{first_job.pk}/').data['project_id'], project_id)
        second_job.status = 'completed'
        second_job.save(update_fields=['status'])
        PipingClass.objects.create(job=second_job, class_code='011422-X')
        self.assertEqual(self.client.post(
            f'/api/v1/spec-customization/paper-spec/jobs/{second_job.pk}/save/'
        ).status_code, 200)
        history = self.client.get(f'/api/v1/spec-customization/projects/{project_id}/jobs/')
        self.assertEqual(history.data['current_saved_job_id'], str(second_job.pk))
        self.assertEqual(PipingClass.objects.filter(job=first_job).count(), 1)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_presigned_completion_reuses_owner_project_by_bytes(self):
        first = self.upload('first.pdf', b'%PDF-1.4 same')
        with patch('apps.spec_customization.views.is_presigned_upload_available', return_value=True), \
                patch('apps.spec_customization.views.fetch_uploaded_to_temp_file',
                      return_value=ContentFile(b'%PDF-1.4 same', name='renamed.pdf')), \
                patch('apps.spec_customization.views.best_effort_delete'):
            response = self.client.post('/api/v1/spec-customization/paper-spec/upload/complete/', {
                's3_key': 'synthetic-stage', 'filename': 'renamed.pdf', 'auto_project': True,
            }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['document']['project_id'], first.data['document']['project_id'])
        self.assertNotEqual(response.data['job']['id'], first.data['job']['id'])

    def test_auto_project_rejects_explicit_project_id(self):
        response = self.client.post('/api/v1/spec-customization/paper-spec/upload/', {
            'file': SimpleUploadedFile('spec.pdf', b'%PDF-1.4 first'),
            'auto_project': '1', 'project_id': '00000000-0000-4000-8000-000000000123',
        }, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SpecProject.objects.count(), 0)

    def test_auto_project_schema_error_falls_back_to_standalone_upload(self):
        with patch(
            'apps.spec_customization.views.SpecProject.objects.filter',
            side_effect=ProgrammingError('relation "spec_customization_specproject" does not exist'),
        ):
            response = self.upload('fallback.pdf', b'%PDF-1.4 fallback')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['document']['project_id'])


class ExtractionDispatchTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='dispatch-owner')
        self.enterContext(patch.object(PaperSpecDocument._meta.get_field('file'), 'storage', RemoteMemoryStorage()))
        self.enterContext(patch('apps.spec_customization.views.normalize_to_pdf', return_value=SimpleNamespace(
            success=True, file=ContentFile(b'synthetic pdf', name='source.pdf'))))
        self.enterContext(patch('apps.spec_customization.views._page_count', return_value=1))
        self.enterContext(patch('apps.spec_customization.views._resolve_project_byok', return_value=('', '', '')))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def ingest(self):
        from .views import _ingest_paper_spec_file
        return _ingest_paper_spec_file(src=SimpleUploadedFile('source.pdf', b'synthetic pdf'),
            project_id=None, title='', document_number='', engineer_name='', user_api_key='',
            user_provider='', user_model='', request_user=self.owner)

    def test_dispatch_waits_for_commit(self):
        with patch('apps.spec_customization.views.extract_paper_spec.delay', return_value=SimpleNamespace(id='queued-task')) as delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.ingest()
                delay.assert_not_called()
            delay.assert_called_once_with(str(response.data['job']['id']))
        self.assertEqual(PaperSpecExtractionJob.objects.get().celery_task_id, 'queued-task')

    def test_broker_failure_is_visible_and_never_runs_inline(self):
        with patch('apps.spec_customization.views.extract_paper_spec') as task:
            task.delay.side_effect = ConnectionError('Synthetic broker outage')
            with self.captureOnCommitCallbacks(execute=True):
                self.ingest()
            task.assert_not_called()
        self.assertEqual(PaperSpecExtractionJob.objects.get().status, 'failed')

    def test_missing_job_retries_instead_of_silent_success(self):
        from .tasks import extract_paper_spec
        with patch.object(extract_paper_spec, 'retry', side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                extract_paper_spec.run('00000000-0000-4000-8000-000000000123')
        retry.assert_called_once_with(countdown=2, max_retries=5)

    def test_storage_failure_is_terminal_and_clears_temporary_keys(self):
        from .tasks import extract_paper_spec
        document = PaperSpecDocument.objects.create(original_filename='missing.pdf', sha256_hash='b'*64)
        job = PaperSpecExtractionJob.objects.create(document=document, user_openai_api_key='synthetic-secret')
        with patch('apps.spec_customization.tasks._get_local_path', side_effect=FileNotFoundError('Synthetic missing file')), patch('apps.spec_customization.tasks._write_progress'):
            result = extract_paper_spec.run(str(job.pk))
        job.refresh_from_db()
        self.assertFalse(result['success'])
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.user_openai_api_key, '')

    def test_provider_configuration_failure_is_visible_on_job(self):
        from .tasks import extract_paper_spec
        from .services.extraction_service import ExtractionProviderConfigurationError
        document = PaperSpecDocument.objects.create(original_filename='source.pdf', sha256_hash='c'*64)
        job = PaperSpecExtractionJob.objects.create(document=document)
        with patch('apps.spec_customization.tasks._get_local_path', return_value=('source.pdf', False)), patch('apps.spec_customization.tasks._write_progress'), patch('apps.spec_customization.tasks.PaperSpecExtractionService') as service:
            service.return_value.get_page_count.return_value = 587
            service.return_value.chunk_ranges.return_value = [(0, 9)]
            service.return_value.extract_chunk.side_effect = ExtractionProviderConfigurationError('Check AI credentials and model settings.')
            extract_paper_spec.run(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_message, 'Check AI credentials and model settings.')

    def test_rejected_ai_does_not_become_header_only_ocr_success(self):
        from .services.extraction_service import PaperSpecExtractionService, ExtractionProviderConfigurationError
        service = PaperSpecExtractionService()
        service.provider_configuration_errors = {'Gemini': 'model unavailable', 'OpenAI': 'authentication failed'}
        with patch.object(service, 'extract_via_text_layer', return_value={'piping_classes': [], 'page_text_chars': 5000}), patch.object(service, 'render_pages_to_jpeg_b64', return_value=['synthetic']), patch.object(service, 'extract_via_gemini', return_value=None), patch.object(service, 'extract_via_openai', return_value=None), patch.object(service, 'extract_via_tesseract') as ocr:
            with self.assertRaisesRegex(ExtractionProviderConfigurationError, '/admin/ai-api-keys'):
                service.extract_chunk('source.pdf', 0, 9)
            ocr.assert_not_called()

    def test_saved_claude_default_drives_provider_and_model(self):
        from .services.extraction_service import PaperSpecExtractionService
        service = PaperSpecExtractionService()
        configurations = [
            {'provider': 'gemini', 'managed': False, 'ready': False, 'model': ''},
            {'provider': 'openai', 'managed': False, 'ready': False, 'model': ''},
            {'provider': 'anthropic', 'managed': True, 'ready': True, 'model': 'saved-claude-model'},
        ]
        with patch('apps.spec_customization.services.extraction_service.get_provider_configuration', side_effect=configurations):
            self.assertEqual(service._configured_engines(), ['pymupdf_text', 'claude_vision', 'tesseract'])
        self.assertEqual(service.config['claude_model'], 'saved-claude-model')

    def test_managed_but_unselected_credentials_fail_without_legacy_fallback(self):
        from .services.extraction_service import PaperSpecExtractionService, ExtractionProviderConfigurationError
        with patch('apps.spec_customization.services.extraction_service.get_provider_configuration', return_value={
            'managed': True, 'ready': False, 'model': '',
        }):
            with self.assertRaisesRegex(ExtractionProviderConfigurationError, 'default saved credential'):
                PaperSpecExtractionService()._configured_engines()


class ProjectCreationRetryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='project-owner', email='project-owner@example.test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.payload = {'name': 'Source project', 'creation_request_id': '00000000-0000-4000-8000-000000000789'}

    def test_repeated_creation_returns_same_project(self):
        first = self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        retry = self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.data['project_id'], retry.data['project_id'])
        self.assertEqual(SpecProject.objects.filter(created_by=self.owner).count(), 1)

    def test_reused_key_with_changed_payload_is_rejected(self):
        self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        changed = {**self.payload, 'name': 'Different project'}
        response = self.client.post('/api/v1/spec-customization/projects/', changed, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SpecProject.objects.filter(created_by=self.owner).count(), 1)

    def test_deleted_project_request_does_not_create_replacement(self):
        self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        SpecProject.objects.filter(created_by=self.owner).delete()
        response = self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(SpecProject.objects.filter(created_by=self.owner).exists())

    def test_same_key_is_scoped_to_actor(self):
        first = self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        other = get_user_model().objects.create_user(username='other-project-owner', email='other-project-owner@example.test')
        self.client.force_authenticate(other)
        second = self.client.post('/api/v1/spec-customization/projects/', self.payload, format='json')
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.data['project_id'], second.data['project_id'])
