"""Guarded manual refresh commits independently of remote I/O and report reads."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.finance.command_center_views import FinanceReceivablesDashboardView
from apps.finance.receivables_source_models import ReceivablesSourceSnapshot, ReceivablesSyncState
from apps.finance.services.receivables_source import import_receivables_source
from apps.finance.services.sharepoint_status import finance_sharepoint_status
from apps.finance.sharepoint_views import FinanceReceivablesRefreshView
from apps.finance import tests_command_center as permission_helpers
from apps.finance.tests_receivables_sharepoint import CONFIGURATION
from apps.finance import tests_receivables_source as workbook_helpers
from apps.invoice_tracker.models import CustomerInvoice
from apps.portfolio.sync import PortfolioSyncError
from apps.rbac.models import Permission, RolePermission, UserPermissionOverride
from apps.rbac.permissions import HasModuleAccess
from apps.rbac.route_guard import secure_module_endpoints


URL = '/api/v1/finance/dashboard/receivables/refresh/'
DASHBOARD_URL = '/api/v1/finance/dashboard/receivables/'
ORDINARY_URL = '/api/v1/finance/dashboard/ordinary-command/'


class OrdinaryFinanceWriteView(APIView):
    """Synthetic sibling proves the exception does not disable normal rollback."""
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance_overview'
    permission_action = 'read'

    def post(self, request):
        ReceivablesSyncState.objects.create(pk=1, last_error='Synthetic uncommitted attempt')
        return Response({'inside_transaction': connection.in_atomic_block}, status=400)


urlpatterns = [
    path(URL.lstrip('/'), FinanceReceivablesRefreshView.as_view()),
    path(DASHBOARD_URL.lstrip('/'), FinanceReceivablesDashboardView.as_view()),
    path(ORDINARY_URL.lstrip('/'), OrdinaryFinanceWriteView.as_view()),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, **{**CONFIGURATION, 'FINANCE_SHAREPOINT_SYNC_ENABLED': True})
class FinanceSharePointRefreshTests(TransactionTestCase):
    grant = permission_helpers.FinanceCommandCenterTests.grant
    workbook = workbook_helpers.ReceivablesSourceTests.workbook

    def setUp(self):
        permission_helpers.FinanceCommandCenterTests.setUp(self)
        self.directory = TemporaryDirectory(prefix='finance-refresh-api-')
        self.addCleanup(self.directory.cleanup)
        self.path = self.workbook([{'G': 'TEST-PROJECT', 'L': 125, 'M': 125,
                                   'R': 'Overdue', 'AA': 25, 'AE': 'AED'}])
        content = self.path.read_bytes()
        self.metadata = {'id': 'finance-item', 'name': 'synthetic-finance.xlsx',
                         'size': len(content), 'eTag': 'synthetic-version'}
        transport = patch('apps.portfolio.sync.SharePointWorkbookClient')
        self.graph_class = transport.start()
        self.addCleanup(transport.stop)
        self.graph = self.graph_class.return_value
        self.graph.metadata.return_value = self.metadata
        self.graph.download.return_value = content

    def authorize(self, *, update=True):
        self.grant('finance_overview', 'finance_outgoing')
        if update:
            for permission in Permission.objects.filter(module__code='finance_outgoing', action='update', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)
        cache.clear()

    def refresh(self, payload=None):
        return self.client.post(URL, {} if payload is None else payload, format='json')

    def test_guarded_refresh_publishes_outside_request_transaction_and_never_changes_operational_invoices(self):
        self.authorize()

        def metadata(*_args):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(ReceivablesSyncState.objects.get(pk=1).sync_token)
            return self.metadata

        self.graph.metadata.side_effect = metadata
        response = self.refresh()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'synchronized')
        sync = response.data['sync']
        self.assertEqual(sync['status'], 'idle')
        self.assertTrue(sync['can_refresh'])
        self.assertIsNotNone(sync['last_checked_at'])
        self.assertEqual(sync['snapshot_id'], ReceivablesSourceSnapshot.objects.get(is_active=True).pk)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(CustomerInvoice.objects.exists())
        self.assertIsNone(ReceivablesSyncState.objects.get(pk=1).sync_token)

    def test_unchanged_check_advances_last_checked_without_fabricating_publication(self):
        self.authorize()
        first = self.refresh().data['sync']
        self.graph.metadata.return_value = None
        self.graph.download.reset_mock()
        checked = timezone.now() + timedelta(minutes=1)
        with patch('apps.finance.services.receivables_sharepoint.timezone.now', return_value=checked):
            response = self.refresh()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'unchanged')
        sync = response.data['sync']
        self.assertEqual(sync['last_checked_at'], checked.isoformat())
        self.assertEqual(sync['snapshot_id'], first['snapshot_id'])
        self.assertEqual(sync['snapshot_published_at'], first['snapshot_published_at'])
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.graph.download.assert_not_called()

    def test_failure_retains_checkpoint_and_previous_snapshot_after_guarded_http_error(self):
        self.authorize()
        first = self.refresh().data['sync']
        self.graph.metadata.side_effect = PortfolioSyncError('SharePoint returned HTTP 403. synthetic-private-detail')
        response = self.refresh()
        self.assertEqual(response.status_code, 502, response.data)
        self.assertNotIn('synthetic-private-detail', str(response.data))
        state = ReceivablesSyncState.objects.get(pk=1)
        self.assertIn('403', state.last_error)
        self.assertIsNotNone(state.last_attempt_at)
        self.assertIsNone(state.sync_token)
        self.assertEqual(response.data['sync']['status'], 'failed')
        self.assertEqual(response.data['sync']['last_checked_at'], first['last_checked_at'])
        self.assertEqual(ReceivablesSourceSnapshot.objects.get(is_active=True).pk, first['snapshot_id'])
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)

    def test_live_lease_returns_busy_without_graph_or_false_success_timestamp(self):
        self.authorize()
        ReceivablesSyncState.objects.create(
            pk=1, sync_token=uuid.uuid4(), sync_expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self.refresh()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'busy')
        self.assertEqual(response.data['sync']['status'], 'syncing')
        self.assertIsNone(response.data['sync']['last_checked_at'])
        self.graph_class.assert_not_called()

    def test_read_only_grants_allow_status_but_not_publication(self):
        self.authorize(update=False)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['can_refresh'])
        self.assertEqual(self.refresh().status_code, 403)
        self.graph_class.assert_not_called()
        self.assertFalse(ReceivablesSyncState.objects.exists())

    def test_overview_only_status_is_restricted_without_reading_finance_source_tables(self):
        self.grant('finance_overview')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'status': 'restricted', 'last_checked_at': None, 'last_attempt_at': None,
            'snapshot_id': None, 'snapshot_published_at': None, 'can_refresh': False,
        })
        self.assertFalse(any('finance_receivables' in row['sql'].lower() for row in queries))
        self.assertEqual(self.refresh().status_code, 403)
        self.graph_class.assert_not_called()

    def test_overview_grant_and_authentication_are_required_before_sync(self):
        self.grant('finance_outgoing')
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.refresh().status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.refresh().status_code, [401, 403])
        self.graph_class.assert_not_called()

    def test_explicit_update_and_source_read_denies_win_over_superuser(self):
        self.authorize()
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        for action in ('update', 'read'):
            with self.subTest(action=action):
                override = UserPermissionOverride.objects.create(
                    user_profile=self.profile,
                    permission=Permission.objects.filter(module__code='finance_outgoing', action=action).first(),
                    allowed=False,
                )
                self.assertEqual(self.refresh().status_code, 403)
                override.delete()
                cache.clear()
        self.graph_class.assert_not_called()

    @override_settings(FINANCE_SHAREPOINT_SYNC_ENABLED=False)
    def test_disabled_connection_cannot_be_started_by_the_refresh_button(self):
        self.authorize()
        response = self.refresh()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['sync']['status'], 'disabled')
        self.assertFalse(response.data['sync']['can_refresh'])
        self.graph_class.assert_not_called()
        self.assertFalse(ReceivablesSyncState.objects.exists())

    def test_request_cannot_replace_server_configured_workbook(self):
        self.authorize()
        for payload in ({'url': 'https://untrusted.invalid/source.xlsx'}, {'drive_id': 'other'}, []):
            with self.subTest(payload=payload):
                self.assertEqual(self.refresh(payload).status_code, 400)
        self.graph_class.assert_not_called()
        self.assertFalse(ReceivablesSyncState.objects.exists())

    def test_report_get_is_read_only_and_adds_status_for_its_selected_snapshot(self):
        self.authorize()
        synced = self.refresh().data['sync']
        self.graph_class.reset_mock()
        response = self.client.get(DASHBOARD_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['sharepoint_sync'], synced)
        self.assertEqual(response.data['sources']['receivables']['snapshot_id'], synced['snapshot_id'])
        self.graph_class.assert_not_called()

    def test_manual_replacement_does_not_inherit_old_remote_check(self):
        self.authorize()
        first = self.refresh().data['sync']
        replacement = self.workbook([{'G': 'REPLACEMENT', 'L': 250, 'M': 250, 'AE': 'AED'}], name='replacement.xlsx')
        published = import_receivables_source(replacement, last_row=None)
        status = finance_sharepoint_status(self.user)
        self.assertEqual(status['snapshot_id'], published['snapshot_id'])
        self.assertNotEqual(status['snapshot_id'], first['snapshot_id'])
        self.assertIsNone(status['last_checked_at'])

    def test_other_guarded_post_still_rolls_back_failed_mutation(self):
        self.grant('finance_overview')
        response = self.client.post(ORDINARY_URL, {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data['inside_transaction'])
        self.assertFalse(ReceivablesSyncState.objects.exists())
