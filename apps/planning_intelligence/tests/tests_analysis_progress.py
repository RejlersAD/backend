"""Analysis progress reflects real chunk work and preserves evidence on failure."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from ..models import DocumentIntelligenceRun, PlanningFile, PlanningJob, PlanningProject
from ..services.document_intelligence import ENGINE_VERSION, run_document_intelligence
from ..services.intelligence import analyze_project
from ..services.operational_jobs import canonical_fingerprint, get_or_create_job
from ..tasks import run_planning_job


PROVIDER_CONFIGURATION = {'provider': 'anthropic', 'model': 'synthetic-analysis-model'}
EMPTY_EXTRACTION = {'text': '{"facts": []}'}


class AnalysisChunkProgressTests(SimpleTestCase):
    def setUp(self):
        self.sources = [PlanningFile(id=index, category='sow', original_filename=f'source-{index}.txt',
                                    extracted_text=f'Operator shall inspect synthetic facility {index}.')
                        for index in (1, 2)]
        self.events = []
        configuration = patch('apps.planning_intelligence.services.project_ai.get_project_ai_config', return_value=PROVIDER_CONFIGURATION)
        provider = patch('apps.planning_intelligence.services.project_ai.call_project_ai', return_value=EMPTY_EXTRACTION)
        budget = patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '16'})
        self.configuration, self.provider = configuration.start(), provider.start()
        budget.start()
        self.addCleanup(configuration.stop)
        self.addCleanup(provider.stop)
        self.addCleanup(budget.stop)

    def collect(self, event):
        self.events.append(deepcopy(event))

    def analyze(self, **kwargs):
        return analyze_project(self.sources, progress_callback=self.collect, **kwargs)

    def ai_events(self, status=None):
        return [event for event in self.events if event['phase'] == 'ai_review'
                and (status is None or event.get('chunk_status') == status)]

    def test_two_chunks_report_waiting_before_each_provider_and_real_completion_counts(self):
        observed = []

        def provider(*args, **kwargs):
            event = self.events[-1]
            self.assertEqual(event['phase'], 'ai_review')
            self.assertEqual(event['chunk_status'], 'waiting')
            observed.append((event['chunk_number'], event['chunks_finished'], event['chunks_processed']))
            return EMPTY_EXTRACTION

        self.provider.side_effect = provider
        result = self.analyze()
        self.assertEqual(observed, [(1, 0, 0), (2, 1, 1)])
        completed = self.ai_events('processed')
        self.assertEqual([(row['chunks_finished'], row['chunks_processed'], row['chunks_total']) for row in completed], [(1, 1, 2), (2, 2, 2)])
        self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
        self.assertFalse(result['ai_processing_coverage']['semantic_coverage_verified'])
        self.assertTrue(all(row['provider'] == 'anthropic' for row in self.ai_events()))

    def test_unconfigured_ai_reports_skipped_work_and_never_calls_provider(self):
        self.configuration.return_value = None
        result = self.analyze()
        events = [row for row in self.events if row['phase'] == 'ai_not_run']
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]['chunks_total'], events[0]['chunks_skipped']), (2, 2))
        self.assertEqual(result['ai_processing_coverage']['chunks_processed'], 0)
        self.assertEqual(result['ai_processing_coverage']['status'], 'not_run')
        self.provider.assert_not_called()

    def test_receiving_heartbeat_reports_characters_without_completing_current_chunk(self):
        def provider(*args, **kwargs):
            kwargs['progress_callback']({'response_characters_received': 12})
            kwargs['progress_callback']({'response_characters_received': 31})
            return EMPTY_EXTRACTION

        self.provider.side_effect = provider
        result = self.analyze()
        receiving = self.ai_events('receiving')
        self.assertEqual([(row['chunk_number'], row['response_characters_received']) for row in receiving],
                         [(1, 12), (1, 31), (2, 12), (2, 31)])
        self.assertEqual([(row['chunks_finished'], row['chunks_processed']) for row in receiving],
                         [(0, 0), (0, 0), (1, 1), (1, 1)])
        self.assertEqual(result['ai_processing_coverage']['chunks_processed'], 2)

    def test_explicit_ai_disabled_does_not_emit_waiting_or_claim_complete_analysis(self):
        result = self.analyze(allow_ai=False)
        self.provider.assert_not_called()
        self.assertFalse(self.ai_events())
        self.assertEqual(result['ai_processing_coverage']['status'], 'not_run')
        self.assertFalse(result['ai_processing_coverage']['semantic_coverage_verified'])
        self.assertTrue(any(row['phase'] == 'ai_not_run' for row in self.events))

    def test_failed_chunks_advance_finished_work_without_claiming_processed_coverage(self):
        self.provider.side_effect = TimeoutError('synthetic provider wait exhausted')
        result = self.analyze()
        failed = self.ai_events('failed')
        self.assertEqual([(row['chunks_finished'], row['chunks_failed'], row['chunks_processed']) for row in failed], [(1, 1, 0), (2, 2, 0)])
        coverage = result['ai_processing_coverage']
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_failed'], coverage['chunks_remaining']), (0, 2, 2))
        self.assertEqual(coverage['status'], 'partial')
        self.assertFalse(coverage['semantic_coverage_verified'])
        self.assertEqual(len(self.ai_events('waiting')), 2)

    def test_cached_resume_reports_cached_chunk_without_repeating_its_provider_call(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            first = self.analyze()
        skipped = self.ai_events('skipped')
        self.assertEqual(len(skipped), 1)
        self.assertEqual((skipped[0]['chunk_number'], skipped[0]['chunks_skipped']), (2, 1))
        self.events.clear()
        second = self.analyze(resume_state=first['ai_checkpoint'])
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual([row['chunk_number'] for row in self.ai_events('waiting')], [2])
        self.assertEqual([row['chunk_number'] for row in self.ai_events('cached')], [1])
        final = self.ai_events()[-1]
        self.assertEqual((final['chunks_finished'], final['chunks_processed'], final['chunks_skipped']), (2, 2, 0))
        self.assertEqual(second['ai_processing_coverage']['calls_this_pass'], 1)
        self.assertEqual(second['ai_processing_coverage']['chunks_remaining'], 0)

    def test_output_limited_chunk_is_partial_and_still_requires_resume(self):
        self.provider.side_effect = [{**EMPTY_EXTRACTION, 'stop_reason': 'max_tokens'}, EMPTY_EXTRACTION]
        result = self.analyze()
        self.assertEqual([row['chunk_number'] for row in self.ai_events('partial')], [1])
        final = self.ai_events()[-1]
        self.assertEqual((final['chunks_finished'], final['chunks_processed'], final['chunks_failed']), (2, 1, 0))
        coverage = result['ai_processing_coverage']
        self.assertEqual((coverage['chunks_partial'], coverage['chunks_remaining']), (1, 1))
        self.assertTrue(coverage['resume_available'])
        self.assertNotIn('0', result['ai_checkpoint']['chunks'])

    def test_progress_events_do_not_contain_source_text_or_response_payload(self):
        self.analyze()
        serialized = str(self.events)
        for source in self.sources:
            self.assertNotIn(source.extracted_text, serialized)
        self.assertNotIn(EMPTY_EXTRACTION['text'], serialized)


class PersistedAnalysisProgressTests(TransactionTestCase):
    def setUp(self):
        self.project = PlanningProject.objects.create(name='Synthetic progress project')
        self.sources = [PlanningFile.objects.create(
            project=self.project, category='sow', file=f'tests/progress-{index}.txt',
            original_filename=f'progress-{index}.txt', parse_status='done',
            extracted_text=f'Operator shall inspect synthetic facility {index}.',
        ) for index in (1, 2)]
        configuration = patch('apps.planning_intelligence.services.project_ai.get_project_ai_config', return_value=PROVIDER_CONFIGURATION)
        provider = patch('apps.planning_intelligence.services.project_ai.call_project_ai', return_value=EMPTY_EXTRACTION)
        basis = patch('apps.planning_intelligence.services.schedule_basis.build_schedule_basis',
                      return_value=SimpleNamespace(id=9001, version=1, readiness={'ready': False}))
        budget = patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '16'})
        self.configuration, self.provider = configuration.start(), provider.start()
        basis.start()
        budget.start()
        self.addCleanup(configuration.stop)
        self.addCleanup(provider.stop)
        self.addCleanup(basis.stop)
        self.addCleanup(budget.stop)

    def job(self, request_data=None):
        return PlanningJob.objects.create(project=self.project, job_type='analyze', request_data=request_data or {})

    def assert_waiting_is_saved(self, job, expected_chunk):
        # This assertion runs inside the mocked provider BEFORE it returns.
        # TransactionTestCase avoids a surrounding test transaction masking
        # a provider call that accidentally holds the database transaction open.
        self.assertFalse(connection.in_atomic_block)
        current = PlanningJob.objects.get(pk=job.pk)
        self.assertEqual(current.status, 'running')
        self.assertGreaterEqual(current.progress, 25)
        self.assertLess(current.progress, 85)
        entry = current.progress_log[-1]
        self.assertEqual(entry['phase'], 'ai_review')
        self.assertEqual(entry['details']['chunk_status'], 'waiting')
        self.assertEqual(entry['details']['chunk_number'], expected_chunk)
        self.assertEqual(entry['details']['chunks_total'], 2)
        self.assertIsNotNone(current.heartbeat_at)
        return current

    def test_task_persists_waiting_before_provider_and_later_persistence_and_basis_phases(self):
        job, observed = self.job(), []

        def provider(*args, **kwargs):
            current = self.assert_waiting_is_saved(job, len(observed) + 1)
            observed.append(current.progress_log[-1]['details']['chunks_processed'])
            return EMPTY_EXTRACTION

        self.provider.side_effect = provider
        result = run_planning_job.run(job.pk)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(observed, [0, 1])
        job.refresh_from_db()
        phases = [row.get('phase') for row in job.progress_log]
        self.assertIn('source_extraction', phases)
        self.assertIn('persistence', phases)
        self.assertIn('basis', phases)
        self.assertLess(phases.index('persistence'), phases.index('basis'))
        self.assertEqual(job.progress, 100)
        self.assertFalse(job.result_data['intelligence']['extraction_summary']['semantic_coverage_verified'])

    def test_two_provider_timeouts_retain_deterministic_facts_and_partial_saved_result(self):
        job, calls = self.job(), []

        def timeout(*args, **kwargs):
            self.assert_waiting_is_saved(job, len(calls) + 1)
            calls.append(True)
            kwargs['error_details'].update(code='timeout', retryable=True)
            return None

        self.provider.side_effect = timeout
        result = run_planning_job.run(job.pk)
        self.assertEqual(result['status'], 'succeeded')
        job.refresh_from_db()
        intelligence = job.result_data['intelligence']
        self.assertEqual(intelligence['ai_processing_coverage']['chunks_failed'], 2)
        self.assertEqual(intelligence['ai_processing_coverage']['chunks_processed'], 0)
        self.assertEqual(intelligence['extraction_summary']['status'], 'partial')
        run = DocumentIntelligenceRun.objects.get(pk=intelligence['document_intelligence_run_id'])
        self.assertEqual(run.facts.filter(fact_type='requirement').count(), 2)
        self.assertEqual(set(run.facts.filter(fact_type='requirement').values_list('value', flat=True)),
                         {source.extracted_text for source in self.sources})
        self.assertIn('partial', job.message)

    def test_stream_heartbeat_is_persisted_without_increasing_chunk_completion(self):
        job, observed = self.job(), []

        def provider(*args, **kwargs):
            before = self.assert_waiting_is_saved(job, len(observed) + 1)
            kwargs['progress_callback']({'response_characters_received': 23})
            after = PlanningJob.objects.get(pk=job.pk)
            self.assertEqual(after.progress, before.progress)
            self.assertEqual(after.progress_log[-1]['details']['chunk_status'], 'receiving')
            self.assertEqual(after.progress_log[-1]['details']['response_characters_received'], 23)
            self.assertEqual(after.progress_log[-1]['details']['chunks_finished'], len(observed))
            self.assertGreaterEqual(after.heartbeat_at, before.heartbeat_at)
            observed.append(after.progress)
            return EMPTY_EXTRACTION

        self.provider.side_effect = provider
        self.assertEqual(run_planning_job.run(job.pk)['status'], 'succeeded')
        self.assertEqual(observed, [25, 50])

    def test_completed_job_from_previous_transport_is_not_reused(self):
        previous_key = canonical_fingerprint({
            'operation': 'analyze-v4', 'project_id': self.project.pk,
            'engine_version': ENGINE_VERSION, 'project_updated_at': self.project.updated_at,
            'files': list(self.project.files.filter(is_deleted=False, parse_status='done').order_by('id').values(
                'id', 'updated_at', 'size_bytes', 'confidence_score',
            )),
        })
        old = PlanningJob.objects.create(
            project=self.project, job_type='analyze', status='succeeded', progress=100,
            idempotency_key=previous_key, result_data={'intelligence': {'extraction_summary': {'status': 'partial'}}},
        )
        fresh, created = get_or_create_job(self.project, 'analyze', {}, None)
        self.assertTrue(created)
        self.assertNotEqual(fresh.pk, old.pk)
        self.assertEqual(fresh.status, 'queued')
        repeated, created = get_or_create_job(self.project, 'analyze', {}, None)
        self.assertFalse(created)
        self.assertEqual(repeated.pk, fresh.pk)
        old.refresh_from_db()
        self.assertEqual(old.status, 'succeeded')
        self.assertEqual(old.result_data['intelligence']['extraction_summary']['status'], 'partial')

    def test_output_limit_split_keeps_progress_stable_and_completes_smaller_sections(self):
        # Project files are read newest first: process the smaller second file
        # before the larger first file reaches its output limit.
        for source, repetitions in zip(self.sources, (400, 100)):
            source.extracted_text = 'Source work.\n' * repetitions
            source.save(update_fields=['extracted_text', 'updated_at'])
        job, calls = self.job(), []

        def provider(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                kwargs['error_details'].update(code='output_limit', stop_reason='max_tokens')
                return None
            return EMPTY_EXTRACTION

        self.provider.side_effect = provider
        self.assertEqual(run_planning_job.run(job.pk)['status'], 'succeeded')
        job.refresh_from_db()
        entries = [row for row in job.progress_log if row.get('phase') == 'ai_review']
        percentages = [row['progress'] for row in entries]
        self.assertEqual(percentages, sorted(percentages))
        split_index = next(index for index, row in enumerate(entries)
                           if row['details']['chunk_status'] == 'splitting')
        self.assertEqual(percentages[split_index], percentages[split_index - 1])
        self.assertIn('smaller sections', entries[split_index]['message'])
        coverage = job.result_data['intelligence']['ai_processing_coverage']
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_failed']), (3, 0))
        self.assertEqual(coverage['status'], 'complete')
        self.assertEqual(len(calls), 4)

    def test_resume_task_reuses_checkpoint_and_reports_waiting_only_for_uncached_chunk(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            previous, _ = run_document_intelligence(self.project)
        old_summary = deepcopy(previous.summary)
        self.provider.reset_mock()
        job = self.job({'resume_run_id': previous.pk})

        def provider(*args, **kwargs):
            current = self.assert_waiting_is_saved(job, 2)
            self.assertEqual(current.progress_log[-1]['details']['chunks_processed'], 1)
            return EMPTY_EXTRACTION

        self.provider.side_effect = provider
        result = run_planning_job.run(job.pk)
        self.assertEqual(result['status'], 'succeeded')
        self.provider.assert_called_once()
        previous.refresh_from_db()
        self.assertEqual(previous.summary, old_summary)
        job.refresh_from_db()
        self.assertEqual(job.result_data['intelligence']['ai_processing_coverage']['chunks_processed'], 2)
        self.assertNotEqual(job.result_data['intelligence']['document_intelligence_run_id'], previous.pk)
