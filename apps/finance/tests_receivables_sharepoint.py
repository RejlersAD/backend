"""Finance synchronization keeps external facts, publication, and checkpoint consistent."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook
import requests

from apps.finance.receivables_source_models import ReceivablesSourceRow, ReceivablesSourceSnapshot, ReceivablesSyncState
from apps.finance.services.receivables_source import (
    SOURCE_HEADERS, import_receivables_source, read_receivables_source,
)
from apps.finance.services.receivables_sharepoint import (
    FinanceSharePointSyncError, finance_sharepoint_configuration, resolve_finance_sharepoint_link,
    sync_finance_sharepoint,
)
from apps.invoice_tracker.models import CustomerInvoice
from apps.portfolio.models import PortfolioSource
from apps.portfolio.sync import PortfolioSyncError, TransientGraphError


CONFIGURATION = {
    'FINANCE_SHAREPOINT_TENANT_ID': 'finance.example.test',
    'FINANCE_SHAREPOINT_CLIENT_ID': 'finance-client',
    'FINANCE_SHAREPOINT_CLIENT_SECRET': 'synthetic-private-secret',
    'FINANCE_SHAREPOINT_DRIVE_ID': 'finance-drive',
    'FINANCE_SHAREPOINT_ITEM_ID': 'finance-item',
    'FINANCE_SHAREPOINT_SYNC_ENABLED': False,
}


@override_settings(**CONFIGURATION)
class FinanceSharePointTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.workbook('remote.xlsx', amount=125)
        self.content = self.path.read_bytes()
        self.metadata = {
            'id': 'finance-item', 'name': 'remote.xlsx', 'size': len(self.content), 'eTag': 'version-1',
        }
        transport = patch('apps.portfolio.sync.SharePointWorkbookClient')
        self.client_class = transport.start()
        self.addCleanup(transport.stop)
        self.client = self.client_class.return_value
        self.client.metadata.return_value = self.metadata
        self.client.download.return_value = self.content

    def workbook(self, name, *, amount):
        path = self.directory / name
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'External Invoice '
        for column, header in SOURCE_HEADERS.items():
            sheet[f'{column}5'] = header
        for column, value in {'A': 'INV-SYNTHETIC', 'B': date(2026, 9, 1), 'E': 'Synthetic account',
                              'F': 'Synthetic company', 'L': amount, 'M': amount, 'N': date(2026, 9, 2),
                              'R': 'Overdue', 'Y': amount, 'AE': 'AED'}.items():
            sheet[f'{column}6'] = value
        workbook.save(path)
        workbook.close()
        return path

    def state(self):
        return ReceivablesSyncState.objects.get(pk=1)

    def assert_released(self):
        self.assertIsNone(self.state().sync_token)
        self.assertIsNone(self.state().sync_expires_at)

    def test_success_populates_existing_reporting_source_with_no_operational_or_portfolio_writes(self):
        result = sync_finance_sharepoint()
        self.assertEqual(result['status'], 'synchronized')
        self.assertEqual(result['row_count'], 1)
        self.assertTrue(result['activated'])
        self.assertEqual(set(result), {'status', 'snapshot_id', 'created', 'activated', 'row_count'})
        state = self.state()
        self.assertEqual(state.remote_snapshot_id, result['snapshot_id'])
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.etag, 'version-1')
        self.assertEqual(state.remote_identity, finance_sharepoint_configuration().identity)
        self.assertIsNotNone(state.last_success_at)
        self.assertEqual(ReceivablesSourceRow.objects.get().payment_status, 'overdue')
        self.assertFalse(CustomerInvoice.objects.exists())
        self.assertFalse(PortfolioSource.objects.exists())
        self.assert_released()

    def test_unchanged_response_reuses_only_matching_active_checkpoint(self):
        first = sync_finance_sharepoint()
        self.client.reset_mock()
        self.client.metadata.return_value = None
        result = sync_finance_sharepoint()
        self.assertEqual(result, {'status': 'unchanged', 'snapshot_id': first['snapshot_id']})
        self.client.metadata.assert_called_once_with('version-1')
        self.client.download.assert_not_called()
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.assertEqual(self.state().generation, 1)
        self.assert_released()

    def test_same_bytes_with_new_etag_reuses_snapshot(self):
        first = sync_finance_sharepoint()
        self.client.metadata.return_value = {**self.metadata, 'eTag': 'version-2'}
        result = sync_finance_sharepoint()
        self.assertEqual(result['snapshot_id'], first['snapshot_id'])
        self.assertFalse(result['created'])
        self.assertFalse(result['activated'])
        self.assertEqual(self.state().etag, 'version-2')
        self.assertEqual(self.state().generation, 1)

    def test_dry_run_downloads_even_when_cached_and_preserves_publication_checkpoint(self):
        sync_finance_sharepoint()
        before = self.state()
        self.client.reset_mock()
        result = sync_finance_sharepoint(dry_run=True)
        self.assertEqual(result['status'], 'validated')
        self.assertIsNone(result['snapshot_id'])
        self.assertFalse(result['activated'])
        self.assertIsNone(self.client.metadata.call_args_list[0].args[0])
        self.client.download.assert_called_once()
        after = self.state()
        for field in ('generation', 'remote_snapshot_id', 'remote_identity', 'etag', 'last_success_at'):
            self.assertEqual(getattr(before, field), getattr(after, field))
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.assert_released()

    def test_first_dry_run_publishes_nothing(self):
        self.assertEqual(sync_finance_sharepoint(dry_run=True)['status'], 'validated')
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())
        self.assertIsNone(self.state().last_success_at)
        self.assertEqual(self.state().etag, '')

    def test_unexpected_304_fails_closed(self):
        self.client.metadata.return_value = None
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'without a matching'):
            sync_finance_sharepoint()
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())
        self.assert_released()

    def test_forbidden_access_is_not_retried_or_published(self):
        self.client.metadata.side_effect = PortfolioSyncError('SharePoint returned HTTP 403; check access and source configuration.')
        with self.assertRaises(PortfolioSyncError) as caught:
            sync_finance_sharepoint()
        self.assertNotIsInstance(caught.exception, TransientGraphError)
        self.client.metadata.assert_called_once()
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())
        self.assertIn('403', self.state().last_error)
        self.assert_released()

    def test_throttling_preserves_retry_metadata(self):
        self.client.metadata.side_effect = TransientGraphError(429, 123)
        with self.assertRaises(TransientGraphError) as caught:
            sync_finance_sharepoint()
        self.assertEqual(caught.exception.retry_after, 123)
        self.assert_released()

    def test_network_errors_are_sanitized(self):
        self.client.metadata.side_effect = requests.RequestException('private signed-url and synthetic-private-secret')
        with self.assertRaises(FinanceSharePointSyncError) as caught:
            sync_finance_sharepoint()
        self.assertNotIn('private', str(caught.exception))
        self.assertNotIn('secret', self.state().last_error)
        self.assert_released()

    def test_invalid_workbook_keeps_previous_source(self):
        previous = import_receivables_source(self.path, last_row=None)
        self.client.download.return_value = b'invalid-private-finance-data'
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'validation or publication failed'):
            sync_finance_sharepoint()
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).pk, previous['snapshot_id'])
        self.assertIsNone(self.state().remote_snapshot_id)
        self.assert_released()

    def test_remote_source_change_during_download_is_rejected(self):
        self.client.metadata.side_effect = [self.metadata, {**self.metadata, 'eTag': 'changed'}]
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'changed during download'):
            sync_finance_sharepoint()
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())
        self.assert_released()

    def test_manual_activation_during_download_wins_over_sync(self):
        manual = self.workbook('manual.xlsx', amount=999)
        # Simulate another committed publisher before the sync's publication
        # transaction. Inject through download to keep the manual transaction
        # independent from the synchronization transaction in this test harness.
        def download(_size):
            import_receivables_source(manual, last_row=None)
            return self.content

        self.client.download.side_effect = download
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'source or synchronization lease changed'):
            sync_finance_sharepoint()
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).file_name, 'manual.xlsx')
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.assertEqual(self.state().generation, 1)
        self.assert_released()

    def test_publication_rechecks_generation_after_workbook_parsing(self):
        # Exercise the exact guarded-import boundary independently: parsing
        # must finish before the generation/lease check runs under its lock.
        token = uuid.uuid4()
        ReceivablesSyncState.objects.create(pk=1, sync_token=token,
                                           sync_expires_at=timezone.now() + timedelta(minutes=1))
        original = read_receivables_source

        def change_generation(*args, **kwargs):
            result = original(*args, **kwargs)
            ReceivablesSyncState.objects.filter(pk=1).update(generation=1)
            return result

        with patch('apps.finance.services.receivables_source.read_receivables_source', side_effect=change_generation):
            with self.assertRaisesMessage(ValueError, 'source or synchronization lease changed'):
                import_receivables_source(self.path, last_row=None, expected_generation=0, expected_sync_token=token)
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())

    def test_A_B_A_activation_invalidates_inflight_sync_and_remote_checkpoint(self):
        sync_finance_sharepoint()
        other = self.workbook('other.xlsx', amount=200)

        def download(_size):
            import_receivables_source(other, last_row=None)
            import_receivables_source(self.path, last_row=None)
            return self.content

        self.client.download.side_effect = download
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'source or synchronization lease changed'):
            sync_finance_sharepoint()
        self.assertEqual(self.state().generation, 3)
        self.assertEqual(self.state().etag, '')
        self.assertIsNone(self.state().remote_snapshot_id)
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 2)

    def test_manual_change_during_304_cannot_be_recorded_as_current(self):
        sync_finance_sharepoint()
        other = self.workbook('other.xlsx', amount=200)

        def metadata(_etag):
            import_receivables_source(other, last_row=None)
            return None

        self.client.metadata.side_effect = metadata
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'source or synchronization lease changed'):
            sync_finance_sharepoint()
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).file_name, 'other.xlsx')
        self.assertEqual(self.state().etag, '')

    def test_expired_lease_rejects_publication(self):
        def download(_size):
            ReceivablesSyncState.objects.filter(pk=1).update(sync_expires_at=timezone.now() - timedelta(seconds=1))
            return self.content

        self.client.download.side_effect = download
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'source or synchronization lease changed'):
            sync_finance_sharepoint()
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())
        self.assert_released()

    def test_checkpoint_save_failure_rolls_back_snapshot_activation(self):
        previous = import_receivables_source(self.workbook('previous.xlsx', amount=10), last_row=None)
        original_save = ReceivablesSyncState.save

        def fail_checkpoint(instance, *args, **kwargs):
            if instance.remote_identity:
                raise RuntimeError('private persistence detail')
            return original_save(instance, *args, **kwargs)

        with patch.object(ReceivablesSyncState, 'save', fail_checkpoint):
            with self.assertRaisesMessage(FinanceSharePointSyncError, 'previous source remains available'):
                sync_finance_sharepoint()
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).pk, previous['snapshot_id'])
        self.assertEqual(self.state().generation, 1)
        self.assertEqual(self.state().etag, '')
        self.assertNotIn('private', self.state().last_error)
        self.assert_released()

    def test_expired_worker_cannot_clear_replacement_lease_or_record_its_error(self):
        replacement = uuid.uuid4()

        def download(_size):
            ReceivablesSyncState.objects.filter(pk=1).update(
                sync_token=replacement, sync_expires_at=timezone.now() + timedelta(minutes=1),
                last_error='Replacement worker state',
            )
            return self.content

        self.client.download.side_effect = download
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'source or synchronization lease changed'):
            sync_finance_sharepoint()
        self.assertEqual(self.state().sync_token, replacement)
        self.assertEqual(self.state().last_error, 'Replacement worker state')
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())

    def test_busy_does_not_steal_a_live_lease(self):
        token = uuid.uuid4()
        ReceivablesSyncState.objects.create(pk=1, sync_token=token,
                                           sync_expires_at=timezone.now() + timedelta(minutes=1))
        self.assertEqual(sync_finance_sharepoint(), {'status': 'busy'})
        self.client.metadata.assert_not_called()
        self.assertEqual(self.state().sync_token, token)

    @override_settings(FINANCE_SHAREPOINT_CLIENT_SECRET='')
    def test_missing_configuration_never_calls_graph(self):
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'configuration is incomplete'):
            sync_finance_sharepoint()
        self.client.metadata.assert_not_called()
        self.assert_released()

    def test_configuration_is_independent_from_portfolio(self):
        configuration = finance_sharepoint_configuration()
        self.assertEqual(configuration.client_id, 'finance-client')
        self.assertEqual(configuration.drive_id, 'finance-drive')
        self.assertEqual(configuration.item_id, 'finance-item')
        self.assertNotIn('synthetic-private-secret', repr(configuration))

    def test_link_resolution_uses_finance_credentials_without_publication(self):
        self.client.resolve_link.return_value = {'drive_id': 'finance-drive', 'item_id': 'finance-item'}
        url = 'https://example.sharepoint.com/sites/finance/_layouts/15/Doc.aspx?sourcedoc=synthetic'
        self.assertEqual(resolve_finance_sharepoint_link(url)['item_id'], 'finance-item')
        self.client.resolve_link.assert_called_once_with(url)
        self.assertFalse(ReceivablesSyncState.objects.exists())
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())

    def test_link_resolution_rejects_untrusted_hosts(self):
        with self.assertRaisesMessage(FinanceSharePointSyncError, 'valid HTTPS'):
            resolve_finance_sharepoint_link('https://example.sharepoint.com.attacker.test/private')
        self.client.resolve_link.assert_not_called()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True, FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=1800)
    def test_scheduled_first_import_is_immediate_and_restart_obeys_checkpoint(self):
        started = timezone.now()
        with patch('apps.finance.services.receivables_sharepoint.timezone.now', return_value=started):
            first = sync_finance_sharepoint(scheduled=True)
        self.assertEqual(first['status'], 'synchronized')
        self.client_class.reset_mock()
        with patch('apps.finance.services.receivables_sharepoint.timezone.now',
                   return_value=started + timedelta(seconds=12, milliseconds=100)):
            second = sync_finance_sharepoint(scheduled=True)
        self.assertEqual(second, {'status': 'not_due', 'retry_after_seconds': 1788})
        self.client_class.assert_not_called()
        self.assertEqual(self.state().last_attempt_at, started)
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        with patch('apps.finance.services.receivables_sharepoint.timezone.now',
                   return_value=started + timedelta(seconds=1800)):
            self.client.metadata.return_value = None
            third = sync_finance_sharepoint(scheduled=True)
        self.assertEqual(third['status'], 'unchanged')
        self.assertEqual(self.state().last_attempt_at, started + timedelta(seconds=1800))

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True, FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=1800)
    def test_manual_sync_remains_immediate_after_a_scheduled_attempt(self):
        sync_finance_sharepoint(scheduled=True)
        self.client.reset_mock()
        self.client.metadata.return_value = None
        self.assertEqual(sync_finance_sharepoint()['status'], 'unchanged')
        self.client.metadata.assert_called_once_with('version-1')

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True, FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=1800)
    def test_failure_keeps_cadence_and_previous_source_across_restarts(self):
        previous = import_receivables_source(self.path, last_row=None)
        started = timezone.now()
        self.client.metadata.side_effect = PortfolioSyncError('SharePoint returned HTTP 403.')
        with patch('apps.finance.services.receivables_sharepoint.timezone.now', return_value=started):
            with self.assertRaises(PortfolioSyncError):
                sync_finance_sharepoint(scheduled=True)
        self.client.reset_mock()
        with patch('apps.finance.services.receivables_sharepoint.timezone.now',
                   return_value=started + timedelta(seconds=10)):
            result = sync_finance_sharepoint(scheduled=True)
        self.assertEqual(result, {'status': 'not_due', 'retry_after_seconds': 1790})
        self.client.metadata.assert_not_called()
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).pk, previous['snapshot_id'])
        self.assertIn('403', self.state().last_error)
        self.assert_released()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True, FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=1800)
    def test_transient_retry_is_bound_to_the_failed_attempt_and_cannot_be_reused(self):
        self.client.metadata.side_effect = TransientGraphError(429, 60)
        with self.assertRaises(TransientGraphError) as caught:
            sync_finance_sharepoint(scheduled=True)
        retry_of = caught.exception.finance_retry_of
        self.assertEqual(retry_of, (self.state().last_attempt_at, self.state().generation))
        self.client.metadata.side_effect = None
        self.assertEqual(sync_finance_sharepoint(scheduled=True, retry_of=retry_of)['status'], 'synchronized')
        self.client.reset_mock()
        self.assertEqual(sync_finance_sharepoint(scheduled=True, retry_of=retry_of), {'status': 'superseded'})
        self.client.metadata.assert_not_called()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True)
    def test_manual_publication_cancels_a_pending_transient_retry(self):
        self.client.metadata.side_effect = TransientGraphError(503)
        with self.assertRaises(TransientGraphError) as caught:
            sync_finance_sharepoint(scheduled=True)
        manual = import_receivables_source(self.path, last_row=None)
        self.client.reset_mock()
        self.assertEqual(sync_finance_sharepoint(scheduled=True, retry_of=caught.exception.finance_retry_of),
                         {'status': 'superseded'})
        self.client.metadata.assert_not_called()
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).pk, manual['snapshot_id'])

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True)
    def test_another_failed_manual_attempt_cancels_a_pending_transient_retry(self):
        self.client.metadata.side_effect = TransientGraphError(503)
        with self.assertRaises(TransientGraphError) as caught:
            sync_finance_sharepoint(scheduled=True)
        self.client.metadata.side_effect = PortfolioSyncError('SharePoint returned HTTP 403.')
        with self.assertRaises(PortfolioSyncError):
            sync_finance_sharepoint()
        self.client.reset_mock()
        self.assertEqual(sync_finance_sharepoint(scheduled=True, retry_of=caught.exception.finance_retry_of),
                         {'status': 'superseded'})
        self.client.metadata.assert_not_called()
        self.assertIn('403', self.state().last_error)
