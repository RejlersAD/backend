from datetime import datetime, timedelta, timezone as utc
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.tests.tests_ai_workforce import WorkforceAdoptionTests
from apps.rbac.ai_measurement_models import AIWorkflowRun, AIWorkforceSnapshot
from apps.rbac.ai_champion_models import AIUsageLog
from apps.rbac.ai_telemetry import workflow_context, record_usage, bind_result, finish_workflow, _ObservedClient, tracked_planning
from apps.rbac.ai_snapshots import capture_workforce, cohort_at
from apps.rbac.ai_workforce_service import workforce_adoption
from apps.rbac.ai_measurement_service import measurement_report, monetary_values, maturity_indicators
from apps.rbac.ai_outcome_service import submit, review
from apps.rbac.ai_outcome_models import AIOutcomeEvidence
from apps.rbac.ai_champion_views import AIChampionViewSet


@override_settings(AI_ADOPTION_MODULE_APPLICATIONS={'workforce_test_ai': ['test-ai']})
class MeasurementTests(TestCase):
    employee = WorkforceAdoptionTests.employee

    def setUp(self):
        WorkforceAdoptionTests.setUp(self)
        self.operator = self.employee('pilot_operator').user
        self.reviewer = self.employee('pilot_reviewer').user

    def make_run(self, key='job', user=None, module='workforce_test_ai'):
        with workflow_context(user or self.operator, module, 'analyze', key) as row:
            record_usage(provider='openai', model='test-model', request_id='provider-request', tokens_input=10, tokens_output=5)
            bind_result('test.Report', key)
            finish_workflow(row, 'completed')
        return row

    def test_idempotent_workflows_and_provider_ids(self):
        first = self.make_run()
        second = self.make_run()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AIUsageLog.objects.filter(workflow=first).count(), 1)
        self.assertEqual(AIWorkflowRun.objects.count(), 1)
        self.assertEqual(AIUsageLog.objects.get(workflow=first).provenance, 'server')

    def test_http_adapter_records_delivery_without_claiming_persisted_output(self):
        from apps.rbac.ai_telemetry import tracked_http
        from rest_framework.response import Response

        @tracked_http('crs_documents')
        def process(request):
            record_usage(provider='openai', model='test', request_id='cleaning')
            return Response({'comments': []})

        request = SimpleNamespace(user=self.operator, method='POST', headers={})
        process(request)
        row = AIWorkflowRun.objects.get(module='crs_documents')
        self.assertEqual(row.status, 'returned')
        self.assertEqual(row.source_id, '')
        self.assertEqual(row.requests.count(), 1)

    def test_worker_adapter_uses_explicit_actor_and_durable_job_identity(self):
        from apps.rbac.ai_telemetry import tracked_user_job

        @tracked_user_job('designiq')
        def extract(self, user_id=None):
            record_usage(provider='openai', model='test', request_id='ocr')
            return {'success': True}

        task = SimpleNamespace(request=SimpleNamespace(id='durable-extraction'))
        extract(task, user_id=self.operator.pk)
        extract(task, user_id=self.operator.pk)
        row = AIWorkflowRun.objects.get(module='designiq')
        self.assertEqual(row.user, self.operator)
        self.assertEqual(row.requests.count(), 1)
        self.assertEqual(row.status, 'returned')

    def test_worker_adapter_records_handled_failure(self):
        from apps.rbac.ai_telemetry import tracked_user_job

        @tracked_user_job('designiq')
        def extract(user_id=None, task_id=None):
            return {'success': False}

        extract(user_id=self.operator.pk, task_id='thread-failure')
        self.assertEqual(AIWorkflowRun.objects.get().status, 'failed')

    def test_cached_sdk_client_does_not_reuse_finished_employee_context(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(id='old', usage=None)
        with workflow_context(self.operator, 'designiq', 'extract') as row:
            proxy = _ObservedClient(client, 'openai', workflow=row)
            proxy.chat.completions.create(model='test')
        proxy.chat.completions.create(model='test')
        self.assertEqual(row.requests.count(), 1)

    @override_settings(AI_ADOPTION_MODULE_APPLICATIONS={'pfd_quality': [], 'crs_documents': [], 'designiq': [], 'pfd_to_pid': []})
    def test_coverage_distinguishes_rules_from_provider_adapters(self):
        report = measurement_report()
        coverage = {r['module']: r for r in report['coverage']}
        self.assertEqual(coverage['pfd_quality']['status'], 'Rules / OCR; no provider calls')
        self.assertEqual(coverage['crs_documents']['status'], 'Instrumented')
        self.assertIn('Response only', coverage['crs_documents']['reconciliation']['status'])
        self.assertEqual(report['totals']['ai_workflows'], 0)

    def test_image_operation_is_observed_without_inventing_token_price(self):
        client = Mock()
        client.images.generate.return_value = SimpleNamespace(id='image-result', usage=SimpleNamespace(prompt_tokens=12))
        with workflow_context(self.operator, 'pfd_to_pid', 'drawing'):
            _ObservedClient(client, 'openai').images.generate(model='dall-e-3')
        self.assertFalse(AIUsageLog.objects.get().pricing_recorded)

    def test_reading_existing_analysis_is_not_a_workflow(self):
        from apps.rbac.ai_telemetry import tracked_http
        from rest_framework.response import Response

        @tracked_http('pfd_to_pid')
        def analyze(request):
            return Response({'cached': True})

        analyze(SimpleNamespace(user=self.operator, method='GET', headers={}))
        self.assertFalse(AIWorkflowRun.objects.exists())

    def test_conversion_links_only_completed_persisted_output(self):
        from apps.pfd_converter.models import PFDDocument, PIDConversion
        document = PFDDocument.objects.create(uploaded_by=self.operator, document_number='measurement-test')
        with workflow_context(self.operator, 'pfd_to_pid', 'generate') as row:
            conversion = PIDConversion.objects.create(pfd_document=document, converted_by=self.operator)
            self.assertEqual(row.source_id, '')
            conversion.status = 'completed'
            conversion.save(update_fields=['status'])
            self.assertEqual(row.source_type, 'pfd_converter.PIDConversion')
            self.assertEqual(row.source_id, str(conversion.pk))

    def test_designiq_thread_fallback_keeps_submitting_employee(self):
        from apps.designiq.views import _run_base_extraction_in_thread

        def extract(*args, **kwargs):
            record_usage(provider='openai', model='test', request_id='thread-sdk')
            return []

        extractor = Mock()
        extractor.extract_from_pdf.side_effect = extract
        def thread(*args, **kwargs):
            return SimpleNamespace(start=lambda: kwargs['target'](**kwargs['kwargs']))

        with patch.dict('sys.modules', {
            'apps.designiq.pid_ocr_extractor_v2': SimpleNamespace(PIDLineExtractorV2=lambda: extractor),
            'apps.designiq.breaker_inference': SimpleNamespace(infer_breakers_for_lines=Mock()),
        }), patch('apps.designiq.views.threading.Thread', side_effect=thread), \
            patch('apps.designiq.views._extract_pid_no_per_page', return_value={}), \
            patch('apps.designiq.views.os.path.exists', return_value=False), \
            patch('apps.designiq.views.open', create=True):
            _run_base_extraction_in_thread('thread-job', 'fixture.pdf', 'fixture.pdf', False, 'onshore', user_id=self.operator.pk)
        row = AIWorkflowRun.objects.get(module='designiq')
        self.assertEqual(row.user, self.operator)
        self.assertEqual(row.status, 'returned')
        self.assertEqual(row.requests.count(), 1)

    def test_completed_result_reference_is_not_replaced_on_replay(self):
        row = self.make_run()
        with workflow_context(self.operator, 'workforce_test_ai', 'analyze', 'job'):
            bind_result('test.Report', 'different-output')
        row.refresh_from_db()
        self.assertEqual(row.source_id, 'job')

    def test_buffered_requests_keep_completion_time_and_effective_price(self):
        from apps.rbac.ai_champion_models import AIPricingConfig
        observed = timezone.now()
        AIPricingConfig.objects.create(provider='openai', model_name='priced', input_cost_per_1k=1, output_cost_per_1k=2,
                                      effective_from=observed - timedelta(days=1))
        AIPricingConfig.objects.create(provider='openai', model_name='priced', input_cost_per_1k=100, output_cost_per_1k=200,
                                      effective_from=observed + timedelta(minutes=1))
        with patch('apps.rbac.ai_telemetry.timezone.now', return_value=observed) as clock:
            with workflow_context(self.operator, 'workforce_test_ai', 'pricing') as row:
                record_usage(provider='openai', model='priced', tokens_input=1000, tokens_output=1000)
                clock.return_value = observed + timedelta(minutes=10)
        usage = row.requests.get()
        self.assertEqual(usage.timestamp, observed)
        self.assertEqual(usage.cost_usd, Decimal('3'))
        self.assertTrue(usage.pricing_recorded)

    def test_sessions_boundary_and_organization_isolation(self):
        first = self.make_run('one')
        second = self.make_run('two')
        self.assertEqual(first.session_id, second.session_id)
        past = timezone.now() - timedelta(minutes=31)
        AIWorkflowRun.objects.update(started_at=past, finished_at=past)
        third = self.make_run('three')
        self.assertNotEqual(first.session_id, third.session_id)
        self.operator.rbac_profile.organization = self.other_org
        self.operator.rbac_profile.save()
        fourth = self.make_run('one')
        self.assertNotEqual(first.pk, fourth.pk)
        self.assertNotEqual(third.session_id, fourth.session_id)

    def test_sdk_failure_is_preserved_and_prompt_is_not_stored(self):
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=RuntimeError('private prompt')))))
        with self.assertRaises(RuntimeError):
            with workflow_context(self.operator, 'workforce_test_ai', 'failure'):
                with transaction.atomic():
                    _ObservedClient(sdk, 'openai').chat.completions.create(model='test-model', messages=[{'content': 'secret prompt'}])
        run = AIWorkflowRun.objects.get()
        self.assertEqual(run.status, 'failed')
        log = run.requests.get()
        self.assertFalse(log.success)
        self.assertEqual(log.error_code, 'RuntimeError')
        self.assertNotIn('secret', str(log.__dict__))

    def test_sdk_return_and_captured_thread_context(self):
        response = SimpleNamespace(id='req-1', usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4))
        sdk = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=response)))
        with workflow_context(self.operator, 'workforce_test_ai', 'google') as row:
            proxy = _ObservedClient(sdk, 'google', workflow=row)
            # Client captures the row for executor threads without inherited ContextVars.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(proxy.models.generate_content, model='gemini-test', contents='private').result()
            self.assertIs(result, response)
            finish_workflow(row, 'completed')
        self.assertEqual(row.requests.get().total_tokens, 16)

    def test_telemetry_failure_does_not_fail_business_operation(self):
        with patch.object(AIWorkflowRun.objects, 'get_or_create', side_effect=RuntimeError('database unavailable')):
            with workflow_context(self.operator, 'workforce_test_ai', 'test') as row:
                self.assertIsNone(row)
        with patch.object(AIUsageLog.objects, 'get_or_create', side_effect=RuntimeError('offline')):
            self.make_run()
        self.assertEqual(AIWorkflowRun.objects.get().status, 'completed')

    def test_nested_planning_workflow_attributes_final_output_once(self):
        project = SimpleNamespace(pk=123)
        @tracked_planning('child')
        def child(project, user=None):
            record_usage(provider='anthropic', model='claude-test')
            return SimpleNamespace(pk=1, _meta=SimpleNamespace(label='planning.DocumentIntelligenceRun')), {}
        @tracked_planning('generate_schedule')
        def parent(project, user=None, input_fingerprint=None):
            child(project, user=user)
            return SimpleNamespace(pk=2, _meta=SimpleNamespace(label='planning.PlanningGeneration'))
        parent(project, user=self.operator, input_fingerprint='one')
        row = AIWorkflowRun.objects.get()
        self.assertEqual(row.source_id, '2')
        self.assertEqual(row.requests.count(), 1)
        self.assertEqual(row.status, 'completed')

    def test_client_cannot_mark_usage_as_server(self):
        request = APIRequestFactory().post('/track/', {'provider': 'openai', 'model_name': 'test', 'tokens_input': 1,
            'tokens_output': 2, 'provenance': 'server', 'workflow': 'fake'}, format='json')
        force_authenticate(request, user=self.operator)
        response = AIChampionViewSet.as_view({'post': 'track_ai_usage'})(request)
        self.assertEqual(response.status_code, 201)
        row = AIUsageLog.objects.get()
        self.assertEqual(row.provenance, 'client')
        self.assertIsNone(row.workflow_id)

    def test_scope_and_no_browser_sessions(self):
        self.make_run()
        other = self.employee('other_pilot', org=self.other_org).user
        self.make_run(user=other)
        AIUsageLog.objects.create(user=self.operator, provider='openai', model_name='test', provenance='client')
        result = measurement_report(30, self.org.pk)
        self.assertEqual(result['totals']['ai_workflows'], 1)
        self.assertEqual(result['totals']['sdk_calls'], 1)
        self.assertEqual(result['totals']['derived_sessions'], 1)
        self.assertIsNone(result['totals']['user_prompts'])
        self.assertEqual(measurement_report(30, self.org.pk, search='missing')['count'], 0)

    def test_snapshots_are_immutable_and_not_backdated(self):
        captured = datetime(2026, 8, 30, 0, 5, tzinfo=utc.utc)
        with patch('apps.rbac.ai_snapshots.timezone.now', return_value=captured):
            capture_workforce()
            self.operator.rbac_profile.department = 'Changed department'
            self.operator.rbac_profile.save()
            capture_workforce()
        snapshot = AIWorkforceSnapshot.objects.get(organization=self.org)
        self.assertEqual(snapshot.people[str(self.operator.pk)]['department'], 'Engineering')
        cohort, _, _, _, basis = cohort_at(captured + timedelta(hours=23), organization_id=self.org.pk)
        self.assertEqual(basis['basis'], 'snapshot')
        self.assertEqual(cohort[self.operator.pk]['department'], 'Engineering')
        self.assertEqual(cohort_at(captured - timedelta(days=1), organization_id=self.org.pk)[4]['basis'], 'current_fallback')
        self.assertEqual(cohort_at(captured + timedelta(hours=37), organization_id=self.org.pk)[4]['basis'], 'current_fallback')

    def test_missing_snapshot_cannot_expose_other_organization(self):
        other = self.employee('private_pilot', org=self.other_org).user
        capture_workforce()
        result = cohort_at(timezone.now(), organization_id=self.org.pk)
        self.assertNotIn(other.pk, result[0])

    def test_outcome_link_currency_and_maturity_evidence(self):
        run = self.make_run()
        payload = {'module': self.module.code, 'workflow': str(run.pk), 'title': 'Automation pilot',
            'task_reference': 'ignored-client-reference', 'evidence_url': 'https://example.test/evidence',
            'comparison': 'Comparable scope and evidence of reusable automation accepted by reviewer.',
            'baseline_minutes': 120, 'ai_minutes': 60, 'review_minutes': 15, 'rework_minutes': 15,
            'measurement': 'measured', 'hourly_rate': '100', 'value_currency': 'AED', 'contribution_type': 'automation_creator'}
        outcome = submit(payload, self.reviewer)
        self.assertEqual(outcome['task_reference'], 'test.Report:job')
        decision = {'id': outcome['id'], 'decision': 'approved', 'reason': 'Validated baseline and reusable automation.', 'comparable_quality_confirmed': True}
        with self.assertRaises(PermissionDenied):
            review(decision, self.operator, AIOutcomeEvidence.objects.all())
        independent = self.employee('independent').user
        review(decision, independent, AIOutcomeEvidence.objects.all())
        self.assertEqual(monetary_values(AIOutcomeEvidence.objects.all())[0]['estimated_capacity_value'], '50.00')
        person = {'user_id': str(self.operator.pk), 'organization_id': str(self.org.pk), 'active_days': 1, 'active_weeks': 1}
        maturity_indicators([person], self.org.pk)
        self.assertEqual(person['maturity_level'], 5)
        with self.assertRaises(ValidationError):
            submit(payload, self.reviewer)

    def test_missing_rate_currency_and_unlinked_maturity_rejected(self):
        from apps.rbac.ai_outcome_service import OutcomeInput
        with self.assertRaises(ValidationError):
            OutcomeInput().validate({'hourly_rate': Decimal('100'), 'value_currency': '', 'contribution_type': 'task'})
        with self.assertRaises(ValidationError):
            OutcomeInput().validate({'contribution_type': 'automation_creator'})

    def test_real_pid_endpoint_links_persisted_report(self):
        from apps.pid_analysis.models import PIDDrawing, PIDAnalysisReport
        from apps.pid_analysis.views import PIDDrawingViewSet
        drawing = PIDDrawing.objects.create(uploaded_by=self.operator, original_filename='pilot.pdf', file='pilot.pdf', file_size=1)
        def validate(**kwargs):
            record_usage(provider='openai', model='test-model')
            return {'issues': [], 'metadata': {}}
        request = APIRequestFactory().post('/pid/analyze_hybrid/')
        force_authenticate(request, user=self.operator)
        storage = PIDDrawing._meta.get_field('file').storage
        with patch.object(storage, 'path', return_value='/tmp/pilot.pdf'), patch.object(storage, 'url', return_value='https://example.test/pilot.pdf'), \
                patch('apps.pid_analysis.pipeline.main_pipeline.PIDValidationPipeline') as pipeline:
            pipeline.return_value.validate.side_effect = validate
            response = PIDDrawingViewSet.as_view({'post': 'analyze_hybrid'})(request, pk=drawing.pk)
        self.assertEqual(response.status_code, 200, response.data)
        run = AIWorkflowRun.objects.get()
        self.assertEqual(run.source_type, 'pid_analysis.PIDAnalysisReport')
        self.assertEqual(run.source_id, str(PIDAnalysisReport.objects.get().pk))
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.requests.count(), 1)

    def test_real_planning_task_replay_does_not_duplicate_measurements(self):
        from apps.planning_intelligence.models import PlanningProject, PlanningJob
        from apps.planning_intelligence.tasks import run_planning_job
        project = PlanningProject.objects.create(name='Measured pilot', created_by=self.operator)
        job = PlanningJob.objects.create(project=project, job_type='preview', requested_by=self.operator)
        def preview(*args, **kwargs):
            record_usage(provider='anthropic', model='test-claude')
            return {'sample_activities': []}
        with patch('apps.planning_intelligence.services.pipeline.preview_schedule', side_effect=preview), \
                patch('apps.planning_intelligence.services.audit.record_event'):
            result = run_planning_job.run(job.pk)
            self.assertEqual(result['status'], 'succeeded')
            replay = run_planning_job.run(job.pk)
        self.assertTrue(replay['idempotent_replay'])
        run = AIWorkflowRun.objects.get()
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.source_id, str(job.pk))
        self.assertEqual(run.requests.count(), 1)

    def test_measurements_endpoint_organization_scope_and_permissions(self):
        from apps.rbac.models import Role
        self.make_run()
        other = self.employee('outside', org=self.other_org).user
        self.make_run(user=other)
        role, _ = Role.objects.get_or_create(code='admin', defaults={'name': 'Administrator'})
        self.operator.rbac_profile.roles.add(role)
        view = AIChampionViewSet.as_view({'get': 'measurements'})
        request = APIRequestFactory().get('/measurements/')
        force_authenticate(request, user=self.operator)
        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        request = APIRequestFactory().get('/measurements/')
        force_authenticate(request, user=other)
        self.assertEqual(view(request).status_code, 403)

    def test_output_reconciliation_detects_missing_workflow_reference(self):
        from apps.planning_intelligence.models import PlanningProject, PlanningGeneration
        from apps.rbac.ai_measurement_service import reconcile_outputs
        run = self.make_run(module='planning_package')
        project = PlanningProject.objects.create(name='Reconciliation', created_by=self.operator)
        generation = PlanningGeneration.objects.create(project=project, generated_by=self.operator)
        scope = AIWorkflowRun.objects.filter(organization=self.org)
        report = reconcile_outputs('planning_package', scope, timezone.now() - timedelta(days=1), self.org.pk)
        self.assertEqual(report['unlinked_outputs'], 1)
        AIWorkflowRun.objects.filter(pk=run.pk).update(source_type='planning_intelligence.PlanningGeneration', source_id=str(generation.pk))
        self.assertEqual(reconcile_outputs('planning_package', scope, timezone.now() - timedelta(days=1), self.org.pk)['unlinked_outputs'], 0)

    def test_client_usage_does_not_enter_new_monthly_candidates(self):
        from apps.rbac.monthly_champion_service import candidates
        now = timezone.now()
        AIUsageLog.objects.create(user=self.operator, model_name='test-model', provider='openai', provenance='client', success=True)
        self.assertEqual(candidates(now.year, now.month), [])
