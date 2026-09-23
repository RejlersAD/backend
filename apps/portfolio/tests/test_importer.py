from datetime import date, timedelta
from unittest.mock import patch
import uuid

from django.test import TestCase
from django.utils import timezone

from apps.portfolio.importer import import_workbook
from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource
from .test_workbook import workbook_bytes


class WorkbookImporterTests(TestCase):
    def test_repeat_hash_reuses_immutable_snapshot(self):
        content = workbook_bytes()
        first = import_workbook(content, original_filename='first.xlsx')
        repeated = import_workbook(content, original_filename='renamed.xlsx')
        self.assertTrue(first['created'])
        self.assertTrue(first['activated'])
        self.assertFalse(repeated['created'])
        self.assertFalse(repeated['activated'])
        self.assertEqual(first['snapshot_id'], repeated['snapshot_id'])
        self.assertEqual(PortfolioSnapshot.objects.count(), 1)
        self.assertEqual(PortfolioRow.objects.count(), 1)
        self.assertEqual(PortfolioSnapshot.objects.get().file_name, 'first.xlsx')

    def test_dry_run_never_creates_source_or_rows(self):
        result = import_workbook(workbook_bytes(), dry_run=True)
        self.assertEqual(result['row_count'], 1)
        self.assertFalse(PortfolioSource.objects.exists())
        self.assertFalse(PortfolioRow.objects.exists())

    def test_failed_and_older_import_leave_last_good_snapshot(self):
        result = import_workbook(workbook_bytes())
        before = PortfolioSource.objects.get()
        with self.assertRaises(ValueError):
            import_workbook(workbook_bytes(rows=[{}, {}]))
        with self.assertRaisesRegex(ValueError, 'older'):
            import_workbook(workbook_bytes(reporting_date=date(2026, 8, 31)))
        after = PortfolioSource.objects.get()
        self.assertEqual(after.active_snapshot_id, result['snapshot_id'])
        self.assertEqual(after.last_success_at, before.last_success_at)
        self.assertEqual(PortfolioSnapshot.objects.count(), 1)

    def test_failed_row_insert_rolls_back_publication(self):
        result = import_workbook(workbook_bytes())
        with patch('apps.portfolio.importer.PortfolioRow.objects.bulk_create', side_effect=ValueError('bad row')):
            with self.assertRaisesRegex(ValueError, 'bad row'):
                import_workbook(workbook_bytes(reporting_date=date(2026, 9, 25)))
        self.assertEqual(PortfolioSource.objects.get().active_snapshot_id, result['snapshot_id'])
        self.assertEqual(PortfolioSnapshot.objects.count(), 1)

    def test_lease_guard_prevents_stale_job_and_manual_interference(self):
        source = PortfolioSource.objects.create(sync_token=uuid.uuid4(), sync_expires_at=timezone.now() + timedelta(minutes=5))
        with self.assertRaisesRegex(ValueError, 'in progress'):
            import_workbook(workbook_bytes())
        with self.assertRaisesRegex(ValueError, 'lease'):
            import_workbook(workbook_bytes(), expected_sync_token=uuid.uuid4())
        result = import_workbook(workbook_bytes(), expected_sync_token=source.sync_token)
        self.assertTrue(result['activated'])
        source.refresh_from_db()
        self.assertIsNotNone(source.sync_token)

    def test_expired_lease_and_remote_cache_cleared_by_manual_import(self):
        source = PortfolioSource.objects.create(sync_token=uuid.uuid4(), sync_expires_at=timezone.now() - timedelta(seconds=1),
                                                etag='old', remote_identity='old-source')
        import_workbook(workbook_bytes())
        source.refresh_from_db()
        self.assertIsNone(source.sync_token)
        self.assertEqual(source.etag, '')
        self.assertEqual(source.remote_identity, '')
