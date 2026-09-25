"""Bounded provider concurrency with coordinator-only persistence and recovery."""
import json
from copy import deepcopy
from threading import Barrier, Event, Lock, get_ident
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from ..models import PlanningFile
from ..services import intelligence, project_ai


EMPTY = {'text': '{"facts": []}', 'stop_reason': 'end_turn'}


class ParallelAIAnalysisTests(SimpleTestCase):
    def setUp(self):
        self.project = SimpleNamespace(pk=7, id=7, ai_settings={'provider': 'anthropic'})
        self.sources = [PlanningFile(pk=index, category='sow', original_filename=f'scope-{index}.txt',
                                    extracted_text=f'Contractor shall prepare Deliverable {index}.')
                        for index in range(1, 5)]
        self.requests, self.events, self.checkpoints = [], [], []
        self.coordinator = get_ident()
        self.lock = Lock()
        self.handler = lambda payload, options: EMPTY
        self.progress_handler = lambda event: None
        for patcher in [
                patch.dict('os.environ', {'PLANNING_AI_CONCURRENCY': '2', 'PLANNING_AI_MAX_CHUNKS': '64'}),
                patch.object(project_ai, 'get_project_ai_config', return_value={'provider': 'anthropic', 'model': 'synthetic'}),
                patch.object(project_ai, 'call_project_ai', side_effect=self.provider)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def provider(self, project, **options):
        self.assertNotEqual(get_ident(), self.coordinator)
        payload = json.loads(options['user_prompt'])
        with self.lock:
            self.requests.append((payload['source_file_id'], payload['character_start']))
        return self.handler(payload, options)

    def progress(self, event):
        self.assertEqual(get_ident(), self.coordinator)
        self.events.append(deepcopy(event))
        self.progress_handler(event)

    def checkpoint(self, value):
        self.assertEqual(get_ident(), self.coordinator)
        self.checkpoints.append(deepcopy(value))

    def analyze(self, **kwargs):
        return intelligence.analyze_project(self.sources, project=self.project,
            progress_callback=self.progress, checkpoint_callback=self.checkpoint, **kwargs)

    def provider_fail(self, options, code, status=None):
        options['error_details'].update(provider='anthropic', code=code, http_status=status)
        return None

    def test_two_requests_overlap_but_callbacks_and_usage_stay_on_coordinator(self):
        rendezvous = Barrier(2, timeout=5)
        active, peak, persisted = 0, 0, []

        def handler(payload, options):
            nonlocal active, peak
            with self.lock:
                active += 1
                peak = max(peak, active)
            rendezvous.wait()
            options['progress_callback']({'response_characters_received': 50})
            options['usage_callback'](provider='anthropic', model='synthetic', tokens_input=10)
            with self.lock:
                active -= 1
            return EMPTY

        self.handler = handler
        with patch('apps.rbac.ai_telemetry.record_usage', side_effect=lambda **row: persisted.append((get_ident(), row))):
            result = self.analyze()
        self.assertEqual(peak, 2)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len(persisted), 4)
        self.assertTrue(all(thread == self.coordinator for thread, _ in persisted))
        self.assertEqual(result['ai_processing_coverage']['status'], 'complete')
        self.assertEqual(result['ai_processing_coverage']['concurrency'], 2)
        self.assertEqual(len(self.checkpoints), 4)
        self.assertTrue(any(event.get('chunk_status') == 'receiving' for event in self.events))

    def test_fast_quota_failure_stops_new_work_but_retains_already_started_success(self):
        both_started, release_success = Barrier(2, timeout=5), Event()

        def handler(payload, options):
            both_started.wait()
            if payload['source_file_id'] == 2:
                return self.provider_fail(options, 'quota_exceeded', 429)
            self.assertTrue(release_success.wait(5))
            return EMPTY

        self.handler = handler
        self.progress_handler = lambda event: release_success.set() if event.get('chunk_status') == 'failed' else None
        try:
            result = self.analyze()
        finally:
            release_success.set()
        coverage = result['ai_processing_coverage']
        self.assertCountEqual(self.requests, [(1, 0), (2, 0)])
        self.assertEqual((coverage['chunks_processed'], coverage['chunks_failed'], coverage['chunks_skipped']), (1, 1, 2))
        self.assertEqual(coverage['pause_error']['code'], 'quota_exceeded')
        self.assertEqual(set(result['ai_checkpoint']['chunks']), {'0'})
        self.assertEqual(self.checkpoints[-1], result['ai_checkpoint'])
        self.requests.clear()
        self.handler = lambda payload, options: EMPTY
        resumed = self.analyze(resume_state=result['ai_checkpoint'])
        self.assertCountEqual(self.requests, [(2, 0), (3, 0), (4, 0)])
        self.assertEqual(resumed['ai_processing_coverage']['status'], 'complete')

    def test_persistent_transient_and_unknown_rejections_pause_after_two_completed_failures(self):
        for code, status in [('timeout', None), ('connection_error', None),
                             ('provider_unavailable', 503), ('request_rejected', 400)]:
            with self.subTest(code=code):
                self.requests.clear()
                both_started = Barrier(2, timeout=5)
                def handler(payload, options):
                    if payload['source_file_id'] <= 2:
                        both_started.wait()
                    return self.provider_fail(options, code, status)
                self.handler = handler
                result = self.analyze()
                # At most one replacement can already be started while the
                # second failure is still in flight; none start after pause.
                self.assertLessEqual(len(self.requests), 3)
                self.assertEqual(result['ai_processing_coverage']['pause_error']['code'], code)
                self.assertTrue(result['ai_processing_coverage']['resume_available'])

    def test_failure_finishing_during_checkpoint_is_observed_before_replacement(self):
        both_started, release_failure = Barrier(2, timeout=5), Event()
        pools = []
        base = project_ai.AnalysisRequests

        class ObservedRequests(base):
            def __init__(self, *args):
                super().__init__(*args)
                pools.append(self)

        def handler(payload, options):
            both_started.wait()
            if payload['source_file_id'] == 1:
                return EMPTY
            self.assertTrue(release_failure.wait(5))
            return self.provider_fail(options, 'credit_balance_exhausted', 400)

        def checkpoint(value):
            self.assertEqual(get_ident(), self.coordinator)
            self.checkpoints.append(deepcopy(value))
            if '0' in value['chunks'] and not release_failure.is_set():
                future = next(future for future, key in pools[0].pending.items() if key == '1')
                release_failure.set()
                future.result(timeout=5)

        self.handler, self.checkpoint = handler, checkpoint
        try:
            with patch.object(project_ai, 'AnalysisRequests', ObservedRequests):
                result = self.analyze()
        finally:
            release_failure.set()
        self.assertCountEqual(self.requests, [(1, 0), (2, 0)])
        self.assertEqual(result['ai_processing_coverage']['chunks_processed'], 1)
        self.assertEqual(result['ai_processing_coverage']['pause_error']['code'], 'credit_balance_exhausted')

    def test_out_of_order_success_is_checkpointed_before_a_slow_request_finishes(self):
        release_first = Event()
        self.sources = self.sources[:2]
        checkpoint_before_first = []

        def handler(payload, options):
            file_id = payload['source_file_id']
            if file_id == 1:
                self.assertTrue(release_first.wait(5))
            return {'text': json.dumps({'facts': [{'type': 'deliverable', 'value': f'Deliverable {file_id}',
                'source_file_id': file_id, 'quote': payload['source_text']}]}), 'stop_reason': 'end_turn'}

        def progress(event):
            if event.get('chunk_number') == 2 and event.get('chunk_status') == 'processed':
                checkpoint_before_first.append(deepcopy(self.checkpoints[-1]))
                release_first.set()

        self.handler, self.progress_handler = handler, progress
        try:
            result = self.analyze()
        finally:
            release_first.set()
        self.assertEqual(set(checkpoint_before_first[0]['chunks']), {'1'})
        self.assertEqual([fact['source_file_id'] for fact in result['ai_evidence_facts']], [1, 2])
        self.assertEqual([unit['source_file_id'] for unit in result['ai_processing_coverage']['chunks']], [1, 2])
        self.handler = lambda payload, options: EMPTY
        self.requests.clear()
        restored = self.analyze(resume_state=checkpoint_before_first[0])
        self.assertEqual(self.requests, [(1, 0)])
        self.assertEqual(restored['ai_processing_coverage']['status'], 'complete')

    def test_parallel_budget_never_overshoots_and_complete_cache_is_copied_only_once(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '3', 'PLANNING_AI_CONCURRENCY': '4'}):
            first = self.analyze()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(first['ai_processing_coverage']['calls_this_pass'], 3)
        self.requests.clear()
        complete = self.analyze(resume_state=first['ai_checkpoint'])
        self.assertEqual(len(self.requests), 1)
        self.requests.clear()
        self.checkpoints.clear()
        replay = self.analyze(resume_state=complete['ai_checkpoint'])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.checkpoints, [complete['ai_checkpoint']])
        self.assertEqual(replay['ai_processing_coverage']['status'], 'complete')

    def test_parallel_adaptive_splits_preserve_disjoint_ranges_and_resume_budget(self):
        self.sources = self.sources[:2]
        for source in self.sources:
            source.extracted_text = 'A' * 120
        self.handler = lambda payload, options: ({'text': '{"facts": [', 'stop_reason': 'max_tokens'}
            if len(payload['source_text']) > 60 else EMPTY)
        with patch.object(intelligence, 'AI_MIN_CHUNK_CHARS', 20), patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '3'}):
            first = self.analyze()
        self.assertEqual(first['ai_processing_coverage']['calls_this_pass'], 3)
        split_keys = set(first['ai_checkpoint']['split_keys'])
        self.assertIn('0', split_keys)
        self.assertTrue(split_keys.issubset({'0', '1'}))
        done = set(first['ai_checkpoint']['chunks'])
        self.requests.clear()
        with patch.object(intelligence, 'AI_MIN_CHUNK_CHARS', 20):
            resumed = self.analyze(resume_state=first['ai_checkpoint'])
        coverage = resumed['ai_processing_coverage']
        self.assertEqual(coverage['status'], 'complete')
        self.assertGreaterEqual(len(self.requests), 4 - len(done))
        self.assertLessEqual(len(self.requests), 4)
        self.assertCountEqual([(unit['source_file_id'], unit['character_start'], unit['character_end'])
                               for unit in coverage['chunks']], [(1, 0, 60), (1, 60, 120), (2, 0, 60), (2, 60, 120)])
        progress = [event['characters_finished'] for event in self.events if event['phase'] == 'ai_review']
        # Each pass is monotonic; a continuation starts its own progress measure.
        self.assertEqual(coverage['characters_processed'], 240)
        self.assertTrue(all(0 <= value <= 240 for value in progress))

    def test_cached_only_mode_retains_unfinished_coverage_and_does_not_submit_requests(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            first = self.analyze()
        self.requests.clear()
        self.checkpoints.clear()
        cached = self.analyze(allow_ai=False, resume_state=first['ai_checkpoint'],
                              resume_coverage=first['ai_processing_coverage'])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.checkpoints, [first['ai_checkpoint']])
        self.assertEqual(cached['ai_processing_coverage']['chunks_processed'], 1)
        self.assertEqual(cached['ai_processing_coverage']['chunks_remaining'], 3)
        self.assertTrue(cached['ai_processing_coverage']['cached_only'])

    def test_completed_retry_keeps_literal_claims_from_a_previous_partial_leaf(self):
        self.sources = self.sources[:1]
        quote = self.sources[0].extracted_text
        self.handler = lambda payload, options: {'text': json.dumps({'facts': [
            {'type': 'deliverable', 'value': 'Deliverable 1', 'source_file_id': 1, 'quote': quote}]}),
            'stop_reason': 'max_tokens'}
        partial = self.analyze()
        self.assertEqual(partial['ai_processing_coverage']['chunks_partial'], 1)
        self.handler = lambda payload, options: EMPTY
        complete = self.analyze(resume_state=partial['ai_checkpoint'])
        self.assertEqual(complete['ai_processing_coverage']['status'], 'complete')
        self.assertEqual([fact['value'] for fact in complete['ai_evidence_facts']], ['Deliverable 1'])
        self.assertIn('0', complete['ai_checkpoint']['partial_responses'])

    def test_resumed_checkpoint_is_saved_before_interruption_of_new_work(self):
        with patch.dict('os.environ', {'PLANNING_AI_MAX_CHUNKS': '1'}):
            previous = self.analyze()
        self.requests.clear()
        self.checkpoints.clear()

        def interrupt(event):
            if event.get('chunk_status') == 'waiting':
                raise RuntimeError('Synthetic interruption before the request')

        self.progress_handler = interrupt
        with self.assertRaisesMessage(RuntimeError, 'Synthetic interruption'):
            self.analyze(resume_state=previous['ai_checkpoint'])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.checkpoints, [previous['ai_checkpoint']])
        self.progress_handler = lambda event: None
        completed = self.analyze(resume_state=self.checkpoints[0])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(completed['ai_processing_coverage']['status'], 'complete')


class ParallelProviderBoundaryTests(SimpleTestCase):
    def test_configuration_default_bounds_and_invalid_values(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(project_ai.analysis_concurrency(), 2)
        for value, expected in [('0', 1), ('1', 1), ('2', 2), ('99', 4), ('bad', 2)]:
            with self.subTest(value=value), patch.dict('os.environ', {'PLANNING_AI_CONCURRENCY': value}):
                self.assertEqual(project_ai.analysis_concurrency(), expected)

    def test_both_provider_adapters_can_defer_telemetry_without_database_work(self):
        user = SimpleNamespace(pk=5)
        deferred = []
        callback = lambda **row: deferred.append(row)
        from ..services import claude_client
        with patch('apps.rbac.ai_telemetry.record_usage') as persist:
            claude_client._log_usage(project=None, user=user, model='synthetic', feature='document_intelligence',
                tokens_input=5, tokens_output=7, latency_ms=9, success=True, error_code='', usage_callback=callback)
            with patch.object(project_ai, '_request_gemini', return_value={**EMPTY, 'tokens_input': 11, 'tokens_output': 13}):
                project_ai._call_gemini(None, {'provider': 'gemini', 'model': 'synthetic'},
                    system_prompt='test', user_prompt='test', max_tokens=50, feature='document_intelligence',
                    user=user, json_output=True, usage_callback=callback)
            persist.assert_not_called()
        self.assertEqual([row['provider'] for row in deferred], ['anthropic', 'gemini'])
        self.assertEqual([row['tokens_input'] for row in deferred], [5, 11])
