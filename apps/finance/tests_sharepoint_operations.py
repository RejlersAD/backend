"""Operator commands and scheduling never imply a live connection or publication."""
import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.finance.services.receivables_sharepoint import FinanceSharePointSyncError
from apps.finance.sharepoint_schedule import finance_sharepoint_beat_schedule
from apps.finance.tasks import sync_receivables_sharepoint
from apps.portfolio.sync import TransientGraphError


class FinanceSharePointOperationsTests(SimpleTestCase):
    def test_schedule_requires_enablement_and_bounds_interval(self):
        self.assertEqual(finance_sharepoint_beat_schedule(), {})
        self.assertEqual(finance_sharepoint_beat_schedule(enabled='false'), {})
        entry = finance_sharepoint_beat_schedule(enabled=True, interval_seconds=10)['finance-sharepoint-sync']
        self.assertEqual(entry['task'], sync_receivables_sharepoint.name)
        self.assertEqual(entry['schedule'], 60)
        self.assertEqual(entry['options']['expires'], 60)
        entry = finance_sharepoint_beat_schedule(enabled=True, interval_seconds='bad')['finance-sharepoint-sync']
        self.assertEqual(entry['schedule'], 3600)

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=False)
    def test_disabled_task_does_not_access_sharepoint_or_database(self):
        with patch('apps.finance.services.receivables_sharepoint.sync_finance_sharepoint') as sync:
            self.assertEqual(sync_receivables_sharepoint.run(), {'status': 'disabled'})
        sync.assert_not_called()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True)
    def test_throttling_uses_bounded_retry_after(self):
        error = TransientGraphError(429, retry_after=123)
        with patch('apps.finance.services.receivables_sharepoint.sync_finance_sharepoint', side_effect=error), \
                patch.object(sync_receivables_sharepoint, 'retry', side_effect=RuntimeError('retry')) as retry:
            with self.assertRaisesMessage(RuntimeError, 'retry'):
                sync_receivables_sharepoint.run()
        retry.assert_called_once_with(exc=error, countdown=123, max_retries=3)

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=True)
    def test_access_failure_is_not_retried(self):
        with patch('apps.finance.services.receivables_sharepoint.sync_finance_sharepoint',
                   side_effect=FinanceSharePointSyncError('SharePoint returned HTTP 403.')), \
                patch.object(sync_receivables_sharepoint, 'retry') as retry:
            with self.assertRaisesMessage(FinanceSharePointSyncError, 'HTTP 403'):
                sync_receivables_sharepoint.run()
        retry.assert_not_called()

    def test_sync_command_forwards_dry_run_and_returns_metadata(self):
        output = io.StringIO()
        result = {'status': 'validated', 'row_count': 2, 'snapshot_id': None,
                  'created': False, 'activated': False}
        with patch('apps.finance.management.commands.sync_finance_sharepoint.sync_finance_sharepoint',
                   return_value=result) as sync:
            call_command('sync_finance_sharepoint', dry_run=True, stdout=output)
        sync.assert_called_once_with(dry_run=True)
        self.assertEqual(json.loads(output.getvalue()), result)

    def test_busy_command_is_not_reported_as_success(self):
        with patch('apps.finance.management.commands.sync_finance_sharepoint.sync_finance_sharepoint',
                   return_value={'status': 'busy'}):
            with self.assertRaisesMessage(CommandError, 'Another Finance workbook'):
                call_command('sync_finance_sharepoint', stdout=io.StringIO())

    def test_resolver_outputs_only_config_identifiers(self):
        output = io.StringIO()
        with patch('apps.finance.management.commands.resolve_finance_sharepoint_link.resolve_finance_sharepoint_link',
                   return_value={'drive_id': 'test-drive', 'item_id': 'test-item'}):
            call_command('resolve_finance_sharepoint_link', stdout=output)
        self.assertEqual(output.getvalue(),
                         'FINANCE_SHAREPOINT_DRIVE_ID=test-drive\nFINANCE_SHAREPOINT_ITEM_ID=test-item\n')
