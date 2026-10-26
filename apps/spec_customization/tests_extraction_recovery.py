"""Provider/result recovery; all provider operations are offline synthetic fixtures."""
import inspect
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import PaperSpecDocument, PaperSpecExtractionJob, PipingClass, PipingClassComponent
from .project_models import SpecProject
from .services.completeness import extraction_completeness
from .services.extraction_service import PaperSpecExtractionService
from .tasks import extract_paper_spec


class ClaudeRequestCompatibilityTests(SimpleTestCase):
    def test_current_sdk_accepts_extraction_request_and_response_is_parsed(self):
        from anthropic.resources.messages import Messages
        signature = inspect.signature(Messages.create)
        requests = []

        def create(**request):
            # Bind to the actual installed SDK without opening a connection.
            signature.bind(None, **request)
            self.assertNotIn('temperature', request)
            requests.append(request)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                'piping_classes': [{'class_code': 'SYN150', 'components': [{'component_type': 'valve'}]}],
            }))], usage=SimpleNamespace(input_tokens=100, output_tokens=25))

        service = PaperSpecExtractionService(user_provider='claude', user_model='synthetic-selected-model')
        with patch.object(service, '_get_claude', return_value=SimpleNamespace(messages=SimpleNamespace(create=create))):
            result = service.extract_via_claude(['synthetic-image'], (4, 5))
        self.assertEqual(requests[0]['model'], 'synthetic-selected-model')
        self.assertEqual(result['piping_classes'][0]['components'][0]['component_type'], 'valve')
        self.assertEqual(result['piping_classes'][0]['_source_pages'], [5, 6])
        self.assertEqual(service._claude_prompt_tokens, 100)
        self.assertEqual(service._claude_completion_tokens, 25)


class ExtractionOutcomeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.document = PaperSpecDocument.objects.create(original_filename='synthetic.pdf', sha256_hash='f' * 64)
        self.job = PaperSpecExtractionJob.objects.create(document=self.document,
            user_openai_api_key='synthetic-key', user_claude_api_key='synthetic-key')
        self.service = self.enterContext(patch('apps.spec_customization.tasks.PaperSpecExtractionService')).return_value
        self.service.get_page_count.return_value = 1
        self.service.chunk_ranges.return_value = [(0, 0)]
        self.service.extract_appendix_a_summary.return_value = {'piping_classes': [], 'pages_scanned': [], 'appendix_pages': []}
        self.service.extract_chunk.return_value = {'piping_classes': [], 'engine_used': 'text'}
        self.service.get_usage_and_cost.return_value = {
            'gemini_prompt_tokens': 0, 'gemini_completion_tokens': 0,
            'openai_prompt_tokens': 100, 'openai_completion_tokens': 25, 'cost_usd': Decimal('0.001'),
        }
        self.enterContext(patch('apps.spec_customization.tasks._get_local_path', return_value=('synthetic.pdf', False)))
        self.progress = self.enterContext(patch('apps.spec_customization.tasks._write_progress'))
        self.enterContext(patch('apps.spec_customization.tasks.validate_extracted_classes', return_value={
            'overall_status': 'needs_retry', 'classes_validated': 0, 'warnings_total': 0,
        }))

    def run_with_classes(self, classes):
        self.service.merge_classes.return_value = classes
        with patch('apps.spec_customization.tasks.process_extracted_classes', return_value=(classes, {'duplicates_removed': 0})):
            result = extract_paper_spec.run(str(self.job.pk))
        self.job.refresh_from_db()
        return result

    def test_zero_rows_fail_while_preserving_usage_and_clearing_keys(self):
        result = self.run_with_classes([])
        self.assertFalse(result['success'])
        self.assertEqual(self.job.status, 'failed')
        self.assertIn('No component records were extracted', self.job.error_message)
        self.assertTrue(self.job.config_snapshot['extraction_outcome']['empty_result'])
        self.assertEqual(self.job.openai_prompt_tokens, 100)
        self.assertEqual(self.job.cost_usd, Decimal('0.001'))
        self.assertEqual(self.job.user_openai_api_key, '')
        self.assertEqual(self.job.user_claude_api_key, '')
        self.assertEqual(self.progress.call_args.kwargs['state'], 'FAILURE')

    def test_header_only_result_is_retained_for_review_but_is_not_completed(self):
        result = self.run_with_classes([{'class_code': 'SYN150', 'class_full_code': 'Synthetic class', 'components': []}])
        self.assertFalse(result['success'])
        self.assertEqual(self.job.piping_classes.count(), 1)
        self.assertEqual(self.job.status, 'failed')
        self.assertEqual(extraction_completeness(self.job)['state'], 'partial')
        self.assertTrue(self.job.config_snapshot['extraction_outcome']['partial_result'])

    def test_persisted_component_result_remains_successful(self):
        result = self.run_with_classes([{'class_code': 'SYN150', 'class_full_code': 'Synthetic class',
            'components': [{'component_type': 'valve', 'size_from': '2'}]}])
        self.assertTrue(result['success'])
        self.assertEqual(self.job.status, 'completed')
        self.assertEqual(self.job.config_snapshot['extraction_outcome']['components_saved'], 1)
        self.assertNotIn('% accuracy', self.job.current_phase)
        self.assertEqual(extraction_completeness(self.job), {
            'state': 'complete', 'failed_chunks': [], 'chunks_total': 1,
            'chunks_done': 1, 'processing_report_available': True,
        })  # Preserve successful-run completeness inputs in frozen manifests.

    def test_partial_rows_keep_coverage_warning_after_components_are_saved(self):
        self.service.extract_chunk.return_value = {
            'piping_classes': [], 'engine_used': 'pymupdf_text',
            'local_report': {'components': 1, 'issues': [{'page': 1, 'reason': 'image_page_requires_ocr'}]},
        }
        result = self.run_with_classes([{'class_code': 'SYN150', 'class_full_code': 'Synthetic class',
            'components': [{'component_type': 'pipe', 'size_from': '2'}]}])
        self.assertTrue(result['success'])
        self.assertEqual(extraction_completeness(self.job)['state'], 'partial')
        self.assertIn('retry before exporting', self.job.error_message)

    def test_historical_completed_empty_job_is_reported_without_rewriting_history(self):
        self.job.status = 'completed'
        self.job.config_snapshot = {'extraction_outcome': {'failed_chunks': []}}
        self.job.save(update_fields=['status', 'config_snapshot'])
        report = extraction_completeness(self.job)
        self.assertEqual(report['state'], 'empty')
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'completed')


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class UploadRecoveryTests(TestCase):
    def setUp(self):
        from .views import _sha256_of_file
        from .services.config import SPEC_EXTRACTION_CONFIG
        self.enterContext(patch.dict(SPEC_EXTRACTION_CONFIG, {'dedupe_by_sha256': True}))
        self.owner = get_user_model().objects.create_user(username='recovery-owner', email='recovery-owner@example.test', password='test')
        self.other = get_user_model().objects.create_user(username='recovery-other', email='recovery-other@example.test', password='test')
        self.project = SpecProject.objects.create(name='Synthetic recovery', created_by=self.owner)
        self.body = b'%PDF-1.4 synthetic source'
        file = SimpleUploadedFile('synthetic.pdf', self.body, content_type='application/pdf')
        self.document = PaperSpecDocument.objects.create(original_filename=file.name, sha256_hash=_sha256_of_file(file),
            project_id=str(self.project.pk), uploaded_by=self.owner)
        self.job = PaperSpecExtractionJob.objects.create(document=self.document, created_by=self.owner,
            status='completed', completed_at=timezone.now(), config_snapshot={'extraction_outcome': {'failed_chunks': []}})
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        for model, field in ((PaperSpecDocument, 'file'),):
            self.enterContext(patch.object(model._meta.get_field(field), 'storage', InMemoryStorage()))
        self.enterContext(patch('apps.spec_customization.views._page_count', return_value=1))
        self.enterContext(patch('apps.spec_customization.views._resolve_project_byok', return_value=('', '', '')))
        self.normalizer = self.enterContext(patch('apps.spec_customization.views.normalize_to_pdf',
            side_effect=lambda src: SimpleNamespace(success=True, file=ContentFile(self.body, name=src.name))))
        self.delay = self.enterContext(patch('apps.spec_customization.views.extract_paper_spec.delay',
            return_value=SimpleNamespace(id='synthetic-task')))

    def upload(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post('/api/v1/spec-customization/paper-spec/upload/', {
                'file': SimpleUploadedFile('synthetic.pdf', self.body, content_type='application/pdf'),
                'project_id': str(self.project.pk),
            }, format='multipart')

    def test_reupload_skips_empty_completed_run_and_preserves_old_job(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['deduped'])
        self.assertNotEqual(response.data['job']['id'], str(self.job.pk))
        self.assertTrue(PaperSpecExtractionJob.objects.filter(pk=self.job.pk).exists())
        self.delay.assert_called_once_with(response.data['job']['id'])

    def test_existing_usable_result_is_still_reused(self):
        piping_class = PipingClass.objects.create(job=self.job, class_code='SYN150')
        PipingClassComponent.objects.create(piping_class=piping_class, component_type='pipe')
        self.assertEqual(extraction_completeness(self.job)['state'], 'complete')
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['deduped'])
        self.assertEqual(response.data['job']['id'], str(self.job.pk))
        self.delay.assert_not_called()

    def test_foreign_project_upload_remains_denied(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.upload().status_code, 403)
        self.delay.assert_not_called()

    def test_partial_result_can_be_retried_without_replacing_its_existing_rows(self):
        piping_class = PipingClass.objects.create(job=self.job, class_code='SYN150')
        component = PipingClassComponent.objects.create(piping_class=piping_class, component_type='pipe')
        self.job.config_snapshot = {'extraction_outcome': {'failed_chunks': [{'page_from': 1, 'page_to': 1}]}}
        self.job.save(update_fields=['config_snapshot'])
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['deduped'])
        self.assertTrue(PipingClassComponent.objects.filter(pk=component.pk).exists())
