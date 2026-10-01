"""The optional process runs only Finance and emits operational metadata."""
import io
import json
import logging
import os
import signal
from threading import Event
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.finance import sharepoint_runtime as runtime
from apps.finance.services.receivables_sharepoint import sync_finance_sharepoint, sync_interval_seconds
from apps.portfolio.sync import PortfolioSyncError, TransientGraphError


SERVICE = 'apps.finance.services.receivables_sharepoint.sync_finance_sharepoint'


class FinanceSharePointRuntimeTests(SimpleTestCase):
    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=False)
    def test_disabled_scheduled_call_does_not_touch_database_or_graph(self):
        with patch('apps.portfolio.sync.SharePointWorkbookClient') as client:
            self.assertEqual(sync_finance_sharepoint(scheduled=True), {'status': 'disabled'})
        client.assert_not_called()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=False)
    def test_disabled_runtime_exits_without_running_etl(self):
        output = io.StringIO()
        with patch(SERVICE) as sync:
            self.assertEqual(runtime.run(Event(), output), 0)
        sync.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'disabled')

    def test_interval_matches_existing_scheduler_bounds(self):
        for configured, expected in [(1800, 1800), (1, 60), ('invalid', 3600)]:
            with self.subTest(configured=configured), \
                    override_settings(FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=configured):
                self.assertEqual(sync_interval_seconds(), expected)

    def test_transient_errors_have_only_three_guarded_retries_and_close_connections_before_wait(self):
        errors = [TransientGraphError(429, 123) for _ in range(4)]
        for number, error in enumerate(errors):
            error.finance_retry_of = ('synthetic-attempt', number)
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        output = io.StringIO()
        with patch(SERVICE, side_effect=errors) as sync, patch('django.db.connections.close_all') as close:
            def wait(delay):
                self.assertEqual(close.call_count, sync.call_count)
                self.assertEqual(delay, 123)
                return False
            stop.wait.side_effect = wait
            self.assertEqual(runtime.sync_cycle(stop, output), {'status': 'failed', 'http_status': 429})
        self.assertEqual(sync.call_count, 4)
        self.assertEqual(sync.call_args_list[0].kwargs, {'scheduled': True})
        for attempt in range(1, 4):
            self.assertEqual(sync.call_args_list[attempt].kwargs,
                             {'scheduled': True, 'retry_of': errors[attempt - 1].finance_retry_of})
        self.assertEqual(stop.wait.call_count, 3)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([record['retry_count'] for record in records], [1, 2, 3])

    def test_retry_backoff_is_bounded_and_success_stops_retries(self):
        errors = [TransientGraphError(503), TransientGraphError(503), TransientGraphError(429, 99999)]
        for number, error in enumerate(errors):
            error.finance_retry_of = ('synthetic-attempt', number)
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = False
        with patch(SERVICE, side_effect=[*errors, {'status': 'unchanged', 'snapshot_id': 2}]), \
                patch('django.db.connections.close_all'):
            self.assertEqual(runtime.sync_cycle(stop, io.StringIO()), {'status': 'unchanged', 'snapshot_id': 2})
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [60, 120, 900])

    def test_unguarded_transient_error_never_bypasses_durable_schedule(self):
        stop = Event()
        with patch(SERVICE, side_effect=TransientGraphError(503)) as sync, \
                patch('django.db.connections.close_all'):
            self.assertEqual(runtime.sync_cycle(stop, io.StringIO())['status'], 'failed')
        self.assertEqual(sync.call_count, 1)

    def test_forbidden_and_unexpected_errors_are_not_retried_or_logged(self):
        for error in [PortfolioSyncError('SharePoint returned HTTP 403.'), RuntimeError('synthetic-private-secret')]:
            with self.subTest(error=type(error).__name__), patch(SERVICE, side_effect=error) as sync, \
                    patch('django.db.connections.close_all'):
                output = io.StringIO()
                self.assertEqual(runtime.sync_cycle(Event(), output), {'status': 'failed'})
            self.assertEqual(sync.call_count, 1)
            self.assertEqual(output.getvalue(), '')

    def test_shutdown_interrupts_retry_wait_and_prevents_another_request(self):
        error = TransientGraphError(503)
        error.finance_retry_of = ('synthetic-attempt', 0)
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.return_value = True
        with patch(SERVICE, side_effect=error) as sync, patch('django.db.connections.close_all') as close:
            self.assertEqual(runtime.sync_cycle(stop, io.StringIO()), {'status': 'stopping'})
        self.assertEqual(sync.call_count, 1)
        close.assert_called_once()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True, FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=1800)
    def test_running_process_obeys_database_due_time_and_stops_during_wait(self):
        stop = Mock(spec=Event)
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        output = io.StringIO()
        with patch(SERVICE, side_effect=[{'status': 'unchanged', 'snapshot_id': 2},
                                        {'status': 'not_due', 'retry_after_seconds': 1729}]) as sync, \
                patch('django.db.connections.close_all') as close:
            self.assertEqual(runtime.run(stop, output), 0)
        self.assertEqual(sync.call_count, 2)
        self.assertEqual(close.call_count, 2)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [60, 1729])
        self.assertEqual([json.loads(line)['status'] for line in output.getvalue().splitlines()],
                         ['starting', 'unchanged', 'not_due', 'stopping'])

    def test_output_only_accepts_known_status_and_numeric_metadata(self):
        output = io.StringIO()
        runtime.emit(output, 'synchronized', row_count=42, snapshot_id=4,
                     error='synthetic-private-secret', source_url='https://private.invalid',
                     http_status='private', retry_count=-2)
        record = json.loads(output.getvalue())
        self.assertEqual(set(record), {'event', 'status', 'at', 'row_count', 'snapshot_id'})
        self.assertNotIn('private', output.getvalue())
        output = io.StringIO()
        runtime.emit(output, 'synthetic-private-secret')
        self.assertEqual(json.loads(output.getvalue())['status'], 'failed')

    def test_main_suppresses_startup_output_and_reports_sanitized_failure(self):
        output, errors = io.StringIO(), io.StringIO()
        startup_flags = {}
        def setup():
            startup_flags.update({name: os.environ.get(name) for name in
                                  ('SPEC_SKIP_CORS_ON_READY', 'RBAC_AUTO_SYNC_MODULES')})
            print('synthetic-private-secret')
            logging.critical('synthetic-private-secret')
            raise RuntimeError('synthetic-private-secret')
        with patch('sys.stdout', output), patch('sys.stderr', errors), patch('django.setup', side_effect=setup), \
                patch.dict(os.environ, {'FINANCE_SHAREPOINT_SYNC_ENABLED': 'true',
                                        'SPEC_SKIP_CORS_ON_READY': 'false', 'RBAC_AUTO_SYNC_MODULES': '1'}):
            self.assertEqual(runtime.main(), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'startup_failed')
        self.assertEqual(startup_flags, {'SPEC_SKIP_CORS_ON_READY': 'true', 'RBAC_AUTO_SYNC_MODULES': '0'})
        self.assertNotIn('private', output.getvalue())
        self.assertEqual(errors.getvalue(), '')

    def test_main_wires_termination_to_the_wait_event_and_restores_handlers(self):
        original = signal.getsignal(signal.SIGTERM)
        def run(stop, _output):
            self.assertFalse(stop.is_set())
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(stop.wait(0))
            return 0
        with patch('django.setup'), patch.object(runtime, 'run', side_effect=run), \
                patch.dict(os.environ, {'FINANCE_SHAREPOINT_SYNC_ENABLED': 'true'}):
            self.assertEqual(runtime.main(), 0)
        self.assertIs(signal.getsignal(signal.SIGTERM), original)

    def test_disabled_entry_point_does_not_initialize_django(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'FINANCE_SHAREPOINT_SYNC_ENABLED': 'false'}), \
                patch('django.setup') as setup, patch('sys.stdout', output):
            self.assertEqual(runtime.main(), 0)
        setup.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'disabled')
