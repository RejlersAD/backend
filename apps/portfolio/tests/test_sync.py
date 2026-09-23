import base64
import pickle
from datetime import date, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch
import uuid

from celery.exceptions import Retry
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
import requests

from apps.portfolio.models import PortfolioSnapshot, PortfolioSource
from apps.portfolio.schedule import portfolio_beat_schedule
from apps.portfolio.sync import (
    MAX_FILE_BYTES, PortfolioSyncError, SharePointConfiguration,
    SharePointWorkbookClient, TransientGraphError, _lease, _retry_after,
    resolve_sharepoint_link, sync_sharepoint,
)
from apps.portfolio.tasks import sync_portfolio_sharepoint


CONFIGURATION = {
    'PORTFOLIO_SHAREPOINT_TENANT_ID': 'tenant.example.com',
    'PORTFOLIO_SHAREPOINT_CLIENT_ID': 'portfolio-client',
    'PORTFOLIO_SHAREPOINT_CLIENT_SECRET': 'fake-test-secret',
    'PORTFOLIO_SHAREPOINT_DRIVE_ID': 'drive-1',
    'PORTFOLIO_SHAREPOINT_ITEM_ID': 'item-1',
}
METADATA = {'id': 'item-1', 'name': 'POC.xlsx', 'eTag': '"v1"', 'size': 4,
            'parentReference': {'driveId': 'drive-1'}, 'file': {'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}}


def response(status=200, *, payload=None, headers=None, content=b'test'):
    result = MagicMock()
    result.status_code = status
    result.headers = headers or {}
    result.json.return_value = payload
    result.iter_content.return_value = [content]
    result.__enter__.return_value = result
    result.__exit__.return_value = False
    return result


@override_settings(**CONFIGURATION)
class SharePointClientTests(SimpleTestCase):
    def graph_client(self):
        return SharePointWorkbookClient(SharePointConfiguration.configured())

    @patch('apps.portfolio.sync.requests.post')
    @patch('apps.portfolio.sync.requests.get')
    def test_download_redirect_does_not_receive_graph_bearer(self, get, post):
        post.return_value = response(payload={'access_token': 'fake-access-token'})
        get.side_effect = [
            response(payload=METADATA),
            response(302, headers={'Location': 'https://tenant.sharepoint.com/download?secret=private-link'}),
            response(headers={'Content-Length': '4'}),
        ]
        client = self.graph_client()
        self.assertEqual(client.metadata()['eTag'], '"v1"')
        self.assertEqual(client.download(4), b'test')
        token_request = post.call_args
        self.assertEqual(token_request.args[0], 'https://login.microsoftonline.com/tenant.example.com/oauth2/v2.0/token')
        self.assertEqual(token_request.kwargs['data']['grant_type'], 'client_credentials')
        self.assertFalse(token_request.kwargs['allow_redirects'])
        self.assertIn('Authorization', get.call_args_list[1].kwargs['headers'])
        self.assertNotIn('Authorization', get.call_args_list[2].kwargs['headers'])
        self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in get.call_args_list))
        post.assert_called_once()

    @patch('apps.portfolio.sync.requests.get')
    def test_untrusted_redirect_is_rejected_without_following(self, get):
        client = self.graph_client()
        client._access_token = 'test-token'
        for target in ('https://tenant.sharepoint.com.evil.example/file', 'http://tenant.sharepoint.com/file',
                       'https://user:pass@tenant.sharepoint.com/file', 'https://127.0.0.1/file'):
            get.reset_mock()
            get.return_value = response(302, headers={'Location': target})
            with self.assertRaisesMessage(PortfolioSyncError, 'unsupported'):
                client.download(4)
            get.assert_called_once()

    @patch('apps.portfolio.sync.requests.get')
    def test_metadata_uses_conditional_etag_and_rejects_wrong_identity(self, get):
        client = self.graph_client()
        client._access_token = 'test-token'
        get.return_value = response(304)
        self.assertIsNone(client.metadata('"v1"'))
        self.assertEqual(get.call_args.kwargs['headers']['If-None-Match'], '"v1"')
        get.return_value = response(payload={**METADATA, 'id': 'other'})
        with self.assertRaisesMessage(PortfolioSyncError, 'different source file'):
            client.metadata()

    @patch('apps.portfolio.sync.requests.get')
    def test_size_limits_apply_to_metadata_headers_and_stream(self, get):
        client = self.graph_client()
        client._access_token = 'test-token'
        get.return_value = response(payload={**METADATA, 'size': MAX_FILE_BYTES + 1})
        with self.assertRaisesMessage(PortfolioSyncError, '25 MB'):
            client.metadata()
        get.return_value = response(headers={'Content-Length': str(MAX_FILE_BYTES + 1)})
        with self.assertRaisesMessage(PortfolioSyncError, '25 MB'):
            client.download(4)
        get.return_value = response(content=b'oversize')
        with patch('apps.portfolio.sync.MAX_FILE_BYTES', 5):
            with self.assertRaisesMessage(PortfolioSyncError, '25 MB'):
                client.download(4)

    @patch('apps.portfolio.sync.requests.get')
    def test_only_throttling_and_server_errors_are_retryable(self, get):
        client = self.graph_client()
        client._access_token = 'test-token'
        for code in (429, 500, 503):
            get.return_value = response(code, headers={'Retry-After': '1200'})
            with self.assertRaises(TransientGraphError) as caught:
                client.metadata()
            self.assertEqual(caught.exception.retry_after, 900)
        for code in (400, 401, 403, 404):
            get.return_value = response(code)
            with self.assertRaises(PortfolioSyncError) as caught:
                client.metadata()
            self.assertNotIsInstance(caught.exception, TransientGraphError)

    @override_settings(PORTFOLIO_SHAREPOINT_DRIVE_ID='', PORTFOLIO_SHAREPOINT_ITEM_ID='')
    @patch('apps.portfolio.sync.requests.post')
    @patch('apps.portfolio.sync.requests.get')
    def test_resolve_link_needs_credentials_only_and_does_not_redeem(self, get, post):
        link = 'https://example.sharepoint.com/:x:/r/personal/example/Documents/POC%20Live.xlsx?e=example'
        post.return_value = response(payload={'access_token': 'fake-access-token'})
        get.return_value = response(payload=METADATA)
        self.assertEqual(resolve_sharepoint_link(link), {'drive_id': 'drive-1', 'item_id': 'item-1'})
        encoded = base64.urlsafe_b64encode(link.encode()).decode().rstrip('=')
        self.assertEqual(get.call_args.args[0], f'https://graph.microsoft.com/v1.0/shares/u!{encoded}/driveItem')
        self.assertNotIn('Prefer', get.call_args.kwargs['headers'])
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    @patch('apps.portfolio.sync.requests.get')
    @patch('apps.portfolio.sync.requests.post')
    def test_invalid_share_link_makes_no_network_request(self, post, get):
        with self.assertRaises(PortfolioSyncError):
            resolve_sharepoint_link('https://example.evil.invalid/file')
        post.assert_not_called()
        get.assert_not_called()


class PortfolioScheduleTests(SimpleTestCase):
    def test_retry_exception_preserves_status_and_delay_when_serialized(self):
        original = TransientGraphError(429, 180)
        restored = pickle.loads(pickle.dumps(original))
        self.assertEqual((restored.status_code, restored.retry_after), (429, 180))
        self.assertEqual(str(restored), str(original))

    def test_schedule_is_opt_in_and_interval_is_bounded(self):
        self.assertEqual(portfolio_beat_schedule(enabled=False), {})
        self.assertEqual(portfolio_beat_schedule(enabled=True, interval_seconds=3600)
                         ['portfolio-sharepoint-sync']['schedule'], 3600)
        self.assertEqual(portfolio_beat_schedule(enabled=True, interval_seconds=1)
                         ['portfolio-sharepoint-sync']['schedule'], 60)
        self.assertEqual(portfolio_beat_schedule(enabled=True, interval_seconds='bad')
                         ['portfolio-sharepoint-sync']['schedule'], 3600)
        self.assertEqual(_retry_after('-1'), 1)
        self.assertIsNone(_retry_after('not-a-date'))

    @override_settings(PORTFOLIO_SYNC_ENABLED=False)
    @patch('apps.portfolio.tasks.sync_sharepoint')
    def test_disabled_task_never_calls_connector(self, sync):
        self.assertEqual(sync_portfolio_sharepoint.run(), {'status': 'disabled'})
        sync.assert_not_called()

    @override_settings(PORTFOLIO_SYNC_ENABLED=True)
    @patch.object(sync_portfolio_sharepoint, 'retry', side_effect=Retry())
    @patch('apps.portfolio.tasks.sync_sharepoint')
    def test_task_retries_transient_http_with_bounded_retry_after(self, sync, retry):
        sync.side_effect = TransientGraphError(429, 123)
        with self.assertRaises(Retry):
            sync_portfolio_sharepoint.run()
        self.assertEqual(retry.call_args.kwargs['countdown'], 123)
        self.assertEqual(retry.call_args.kwargs['max_retries'], 3)

    @override_settings(PORTFOLIO_SYNC_ENABLED=True)
    @patch.object(sync_portfolio_sharepoint, 'retry')
    @patch('apps.portfolio.tasks.sync_sharepoint')
    def test_permanent_failure_is_not_retried(self, sync, retry):
        sync.side_effect = PortfolioSyncError('Validation failed.')
        with self.assertRaises(PortfolioSyncError):
            sync_portfolio_sharepoint.run()
        retry.assert_not_called()


@override_settings(**CONFIGURATION)
class PortfolioSyncTests(TestCase):
    def setUp(self):
        self.source = PortfolioSource.objects.create(key='poc')
        self.client_patch = patch('apps.portfolio.sync.SharePointWorkbookClient')
        self.client = self.client_patch.start().return_value
        self.addCleanup(self.client_patch.stop)
        self.client.metadata.side_effect = [METADATA, METADATA]
        self.client.download.return_value = b'test'

    def active_snapshot(self, *, remote=True):
        from apps.portfolio.workbook import PARSER_VERSION

        snapshot = PortfolioSnapshot.objects.create(
            source=self.source, sha256='a' * 64, parser_version=PARSER_VERSION, file_name='test.xlsx',
            reporting_date=date(2026, 9, 18), row_count=0)
        self.source.active_snapshot = snapshot
        self.source.etag = '"published"'
        self.source.remote_identity = SharePointConfiguration.configured().identity if remote else ''
        self.source.save()
        return snapshot

    @patch('apps.portfolio.sync.import_workbook')
    def test_parser_upgrade_downloads_even_when_source_etag_is_unchanged(self, importer):
        snapshot = self.active_snapshot()
        snapshot.parser_version = 'previous-parser'
        snapshot.save(update_fields=['parser_version'])
        importer.return_value = {'dry_run': True, 'row_count': 1}
        result = sync_sharepoint(dry_run=True)
        self.assertEqual(result['status'], 'validated')
        self.assertEqual(self.client.metadata.call_args_list[0].args, (None,))
        self.client.download.assert_called_once()
        importer.assert_called_once()

    def test_held_lease_prevents_overlapping_network_work(self):
        acquired = _lease('poc')
        self.assertIsNotNone(acquired)
        self.assertEqual(sync_sharepoint()['status'], 'busy')
        self.client.metadata.assert_not_called()

    def test_expired_lease_is_replaced(self):
        prior = uuid.uuid4()
        PortfolioSource.objects.filter(pk=self.source.pk).update(
            sync_token=prior, sync_expires_at=timezone.now() - timedelta(seconds=1))
        source, current = _lease('poc')
        self.assertNotEqual(current, prior)
        self.assertGreater(source.sync_expires_at, timezone.now())

    @patch('apps.portfolio.sync.import_workbook')
    def test_first_remote_fetch_ignores_local_etag_and_passes_publication_guard(self, importer):
        self.source.etag = '"old"'
        self.source.remote_identity = 'different-origin'
        self.source.save()
        importer.return_value = {'dry_run': True, 'row_count': 1}
        result = sync_sharepoint(dry_run=True)
        self.assertEqual(result['status'], 'validated')
        self.assertEqual(self.client.metadata.call_args_list[0].args, (None,))
        self.assertIsInstance(importer.call_args.kwargs['expected_sync_token'], uuid.UUID)
        self.source.refresh_from_db()
        self.assertEqual(self.source.etag, '"old"')
        self.assertIsNone(self.source.sync_token)
        self.assertIsNone(self.source.last_success_at)

    @patch('apps.portfolio.sync.import_workbook')
    def test_changed_remote_version_is_rejected_before_import(self, importer):
        self.client.metadata.side_effect = [METADATA, {**METADATA, 'eTag': '"v2"'}]
        with self.assertRaisesMessage(PortfolioSyncError, 'changed during download'):
            sync_sharepoint()
        importer.assert_not_called()
        self.source.refresh_from_db()
        self.assertEqual(self.source.etag, '')
        self.assertIsNone(self.source.sync_token)

    @patch('apps.portfolio.sync.import_workbook')
    def test_failed_import_keeps_previous_etag_and_sanitizes_error(self, importer):
        self.source.etag = '"previous"'
        self.source.save()
        importer.side_effect = ValueError('private source content and secret=do-not-record')
        with self.assertRaisesMessage(PortfolioSyncError, 'validation failed'):
            sync_sharepoint()
        self.source.refresh_from_db()
        self.assertEqual(self.source.etag, '"previous"')
        self.assertNotIn('do-not-record', self.source.last_error)
        self.assertIsNone(self.source.last_success_at)
        self.assertIsNone(self.source.sync_token)

    @patch('apps.portfolio.sync.import_workbook')
    def test_stale_worker_cannot_import_or_release_new_workers_lease(self, importer):
        new_token = uuid.uuid4()

        def steal_lease(_size):
            PortfolioSource.objects.filter(pk=self.source.pk).update(
                sync_token=new_token, sync_expires_at=timezone.now() + timedelta(minutes=15))
            return b'test'

        self.client.download.side_effect = steal_lease
        with self.assertRaisesMessage(PortfolioSyncError, 'lease expired'):
            sync_sharepoint()
        importer.assert_not_called()
        self.source.refresh_from_db()
        self.assertEqual(self.source.sync_token, new_token)
        self.assertEqual(self.source.last_error, '')

    def test_304_without_same_origin_snapshot_is_rejected(self):
        self.client.metadata.side_effect = [None]
        with self.assertRaisesMessage(PortfolioSyncError, 'matching published source'):
            sync_sharepoint()

    @patch('apps.portfolio.sync.import_workbook')
    def test_304_with_same_origin_preserves_active_snapshot_without_import(self, importer):
        prior = self.active_snapshot()
        self.client.metadata.side_effect = [None]
        self.assertEqual(sync_sharepoint()['status'], 'unchanged')
        self.client.metadata.assert_called_once_with('"published"')
        self.client.download.assert_not_called()
        importer.assert_not_called()
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_snapshot_id, prior.pk)
        self.assertEqual(self.source.etag, '"published"')
        self.assertIsNotNone(self.source.last_success_at)
        self.assertIsNone(self.source.sync_token)

    @patch('apps.portfolio.sync.import_workbook')
    def test_same_origin_etag_is_committed_only_after_successful_publication(self, importer):
        prior = self.active_snapshot()
        importer.return_value = {'snapshot_id': prior.pk, 'created': False, 'activated': False}
        result = sync_sharepoint()
        self.assertEqual(result['status'], 'synchronized')
        self.source.refresh_from_db()
        self.assertEqual(self.source.etag, '"v1"')
        self.assertEqual(self.source.active_snapshot_id, prior.pk)
        self.assertEqual(self.source.remote_identity, SharePointConfiguration.configured().identity)

    @patch('apps.portfolio.sync.import_workbook')
    def test_active_local_snapshot_forces_fetch_even_if_etag_is_present(self, importer):
        prior = self.active_snapshot(remote=False)
        importer.return_value = {'snapshot_id': prior.pk, 'created': False, 'activated': False}
        self.assertEqual(sync_sharepoint()['status'], 'synchronized')
        self.assertEqual(self.client.metadata.call_args_list[0].args, (None,))
        self.client.download.assert_called_once_with(4)

    @patch('apps.portfolio.sync.import_workbook')
    def test_failed_remote_import_retains_active_snapshot(self, importer):
        prior = self.active_snapshot()
        importer.side_effect = ValueError('The source report is older than the active snapshot.')
        with self.assertRaises(PortfolioSyncError):
            sync_sharepoint()
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_snapshot_id, prior.pk)
        self.assertEqual(self.source.etag, '"published"')

    @patch('apps.portfolio.sync.import_workbook')
    def test_final_publication_guard_failure_rolls_back_import_and_version_marker(self, importer):
        prior = self.active_snapshot()

        def incomplete_publication(*args, **kwargs):
            snapshot = PortfolioSnapshot.objects.create(
                source=self.source, sha256='b' * 64, parser_version='1.0', file_name='changed.xlsx',
                reporting_date=date(2026, 9, 19), row_count=0)
            PortfolioSource.objects.filter(pk=self.source.pk).update(active_snapshot=snapshot)
            return {'snapshot_id': -1, 'created': True, 'activated': True}

        importer.side_effect = incomplete_publication
        with self.assertRaisesMessage(PortfolioSyncError, 'not published'):
            sync_sharepoint()
        self.source.refresh_from_db()
        self.assertEqual(self.source.active_snapshot_id, prior.pk)
        self.assertEqual(self.source.etag, '"published"')
        self.assertEqual(self.source.snapshots.count(), 1)

    def test_network_error_does_not_store_url_or_token(self):
        self.client.metadata.side_effect = requests.ConnectionError('https://private.example/?token=secret')
        with self.assertRaisesMessage(PortfolioSyncError, 'connection failed'):
            sync_sharepoint()
        self.source.refresh_from_db()
        self.assertNotIn('secret', self.source.last_error)
        self.assertNotIn('private.example', self.source.last_error)

    @patch('apps.portfolio.management.commands.sync_portfolio_sharepoint.sync_sharepoint')
    def test_command_dry_run_is_explicit_and_prints_no_source_details(self, sync):
        sync.return_value = {'status': 'validated'}
        output = StringIO()
        call_command('sync_portfolio_sharepoint', '--dry-run', stdout=output)
        sync.assert_called_once_with(source_key='poc', dry_run=True)
        self.assertEqual(output.getvalue().strip(), 'Portfolio synchronization: validated.')
