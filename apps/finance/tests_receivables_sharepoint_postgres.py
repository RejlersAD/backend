"""Real row-lock races; run only against an isolated PostgreSQL test database."""
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock, Thread
import traceback
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook

from apps.finance.receivables_source_models import ReceivablesSourceSnapshot, ReceivablesSyncState
from apps.finance.services.receivables_source import SOURCE_HEADERS, import_receivables_source
from apps.finance.services.receivables_sharepoint import FinanceSharePointSyncError, _lease, sync_finance_sharepoint


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL transactions and row locks.')
@override_settings(
    FINANCE_SHAREPOINT_TENANT_ID='finance.example.test',
    FINANCE_SHAREPOINT_CLIENT_ID='synthetic-finance-client',
    FINANCE_SHAREPOINT_CLIENT_SECRET='synthetic-finance-secret',
    FINANCE_SHAREPOINT_DRIVE_ID='synthetic-finance-drive',
    FINANCE_SHAREPOINT_ITEM_ID='synthetic-finance-item',
    FINANCE_SHAREPOINT_SYNC_ENABLED=False,
)
class FinanceSharePointPostgresTests(TransactionTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.remote_path = self.workbook('remote.xlsx', 100)
        self.manual_path = self.workbook('manual.xlsx', 200)
        self.content = self.remote_path.read_bytes()
        self.metadata = {
            'id': 'synthetic-finance-item', 'name': 'remote.xlsx',
            'size': len(self.content), 'eTag': 'synthetic-v1',
        }

    def workbook(self, name, amount):
        path = self.directory / name
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'External Invoice '
        for column, header in SOURCE_HEADERS.items():
            sheet[f'{column}5'] = header
        for column, value in {
            'A': 'SYNTHETIC-INVOICE', 'B': date(2026, 9, 1), 'E': 'Synthetic account',
            'F': 'Synthetic customer', 'L': amount, 'M': amount,
            'N': date(2026, 9, 2), 'R': 'Overdue', 'Y': amount, 'AE': 'AED',
        }.items():
            sheet[f'{column}6'] = value
        workbook.save(path)
        workbook.close()
        return path

    @staticmethod
    def await_event(event, description):
        if not event.wait(timeout=10):
            raise AssertionError(f'Timed out waiting for {description}.')

    def run_workers(self, workers):
        """Use distinct sessions, bounded DB waits, and report every thread error."""
        results, errors, backend_pids = {}, [], {}
        result_lock = Lock()

        def run(name, work):
            close_old_connections()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    process_id = cursor.fetchone()[0]
                with result_lock:
                    backend_pids[name] = process_id
                result = work()
                with result_lock:
                    results[name] = result
            except BaseException as exc:
                with result_lock:
                    errors.append((name, exc, traceback.format_exc()))
            finally:
                connections['default'].close()

        threads = [Thread(target=run, args=(name, work), name=f'finance-sync-{name}', daemon=True)
                   for name, work in workers.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads), 'A database worker exceeded its deadline.')
        if errors:
            self.fail('\n'.join(f'{name}: {error}\n{stack}' for name, error, stack in errors))
        self.assertEqual(set(results), set(workers))
        self.assertEqual(len(set(backend_pids.values())), len(workers), 'Workers must use independent PostgreSQL sessions.')
        return results

    def test_concurrent_first_publications_serialize_singleton_and_keep_one_active_snapshot(self):
        first_lock = Barrier(2, timeout=10)
        original_locked = ReceivablesSyncState.locked

        def synchronized_lock(cls):
            first_lock.wait()
            return original_locked()

        self.assertFalse(ReceivablesSyncState.objects.exists())
        with patch.object(ReceivablesSyncState, 'locked', classmethod(synchronized_lock)):
            results = self.run_workers({
                'first': lambda: import_receivables_source(self.remote_path, last_row=None),
                'second': lambda: import_receivables_source(self.manual_path, last_row=None),
            })
        self.assertTrue(all(result['created'] and result['activated'] for result in results.values()))
        self.assertEqual(ReceivablesSyncState.objects.count(), 1)
        self.assertEqual(ReceivablesSyncState.objects.get(pk=1).generation, 2)
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 2)
        self.assertEqual(ReceivablesSourceSnapshot.objects.filter(is_active=True).count(), 1)
        self.assertEqual(set(ReceivablesSourceSnapshot.objects.values_list('pk', flat=True)),
                         {result['snapshot_id'] for result in results.values()})

    def test_sync_cannot_publish_after_separate_manual_transaction_commits(self):
        download_started, manual_committed = Event(), Event()

        def download(_size):
            download_started.set()
            self.await_event(manual_committed, 'manual publication commit')
            return self.content

        def sync_worker():
            try:
                sync_finance_sharepoint()
            except FinanceSharePointSyncError as exc:
                return str(exc)
            raise AssertionError('The stale synchronization unexpectedly published.')

        def manual_worker():
            self.await_event(download_started, 'the synchronization download')
            result = import_receivables_source(self.manual_path, last_row=None)
            # The service has exited its atomic block in this independent session.
            self.assertFalse(connections['default'].in_atomic_block)
            manual_committed.set()
            return result

        with patch('apps.portfolio.sync.SharePointWorkbookClient') as transport:
            transport.return_value.metadata.return_value = self.metadata
            transport.return_value.download.side_effect = download
            results = self.run_workers({'sync': sync_worker, 'manual': manual_worker})
        self.assertIn('source or synchronization lease changed', results['sync'])
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).pk,
                         results['manual']['snapshot_id'])
        state = ReceivablesSyncState.objects.get(pk=1)
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.etag, '')
        self.assertIsNone(state.remote_snapshot_id)
        self.assertIsNone(state.sync_token)

    def test_expired_worker_cannot_publish_release_or_overwrite_replacement_lease(self):
        download_started, lease_replaced = Event(), Event()

        def download(_size):
            download_started.set()
            self.await_event(lease_replaced, 'replacement lease commit')
            return self.content

        def original_worker():
            try:
                sync_finance_sharepoint()
            except FinanceSharePointSyncError as exc:
                return str(exc)
            raise AssertionError('The expired synchronization unexpectedly published.')

        def replacement_worker():
            self.await_event(download_started, 'the original synchronization download')
            with transaction.atomic():
                current = ReceivablesSyncState.locked()
                original_token = current.sync_token
                current.sync_expires_at = timezone.now() - timedelta(seconds=1)
                current.save(update_fields=['sync_expires_at'])
            acquired = _lease()
            if acquired is None:
                raise AssertionError('The expired lease could not be acquired.')
            replacement, _active_id = acquired
            self.assertNotEqual(replacement.sync_token, original_token)
            ReceivablesSyncState.objects.filter(pk=1, sync_token=replacement.sync_token).update(
                last_error='Synthetic replacement worker status',
            )
            self.assertFalse(connections['default'].in_atomic_block)
            lease_replaced.set()
            return replacement.sync_token

        with patch('apps.portfolio.sync.SharePointWorkbookClient') as transport:
            transport.return_value.metadata.return_value = self.metadata
            transport.return_value.download.side_effect = download
            results = self.run_workers({'original': original_worker, 'replacement': replacement_worker})
        self.assertIn('source or synchronization lease changed', results['original'])
        state = ReceivablesSyncState.objects.get(pk=1)
        self.assertEqual(state.sync_token, results['replacement'])
        self.assertGreater(state.sync_expires_at, timezone.now())
        self.assertEqual(state.last_error, 'Synthetic replacement worker status')
        self.assertEqual(state.generation, 0)
        self.assertFalse(ReceivablesSourceSnapshot.objects.exists())
