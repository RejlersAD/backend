"""Opportunity document explorer regressions; synthetic transport only."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.client import closing_iterator_wrapper
import requests
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.action_policy import operation_action
from apps.rbac.models import RolePermission
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.sales.models import Deal, OpportunityAuditEvent, OpportunityWorkspaceUpload
from apps.sales.opportunity_workspace import MAX_DOWNLOAD_BYTES
from apps.sales.tests.test_opportunity_workspace import CONFIG, WorkspaceFixtures
from apps.sales.views import DealViewSet
from apps.sales.workspace_graph import WorkspaceError, WorkspaceGraph, workspace_config


@override_settings(**CONFIG)
class WorkspaceDocumentTests(WorkspaceFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.workspace = self.ready()
        self.folder_id = self.workspace.folders['proposal']['id']
        self.item = self.graph.upload(self.folder_id, 'Technical Proposal.docx', b'bytes')
        self.item.update({
            'file': {'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
            'publication': {'versionId': '3.0', 'level': 'published'},
            'createdDateTime': '2026-09-30T12:00:00Z', 'eTag': 'synthetic-etag',
            'createdBy': {'user': {'displayName': 'Synthetic creator'}},
            'lastModifiedBy': {'user': {'displayName': 'Synthetic editor'}},
        })
        self.file_url = self.url + 'folders/proposal/files/' + self.item['id'] + '/'
        self.graph.versions = MagicMock(return_value={'value': [{
            'id': '3.0', 'size': 5, 'lastModifiedDateTime': '2026-10-01T00:00:00Z',
            'lastModifiedBy': {'user': {'displayName': 'Synthetic editor'}},
            'publication': {'level': 'published'},
        }, {'id': '2.0', 'size': 4}]})
        self.graph.download = MagicMock(side_effect=lambda *args: BytesIO(b'bytes'))

    def test_details_listing_and_versions_show_only_available_source_metadata_without_writes(self):
        before_audits = OpportunityAuditEvent.objects.count()
        before_updated = self.workspace.updated_at
        details = self.api.get(self.file_url)
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.data['version'], '3.0')
        self.assertEqual(details.data['publication_level'], 'published')
        self.assertEqual(details.data['created_by'], 'Synthetic creator')
        self.assertEqual(details.data['modified_by'], 'Synthetic editor')
        self.assertTrue(details.data['can_download'])
        self.assertNotIn('reviewer', details.data)
        self.assertNotIn('owner', details.data)
        self.assertNotIn('status', details.data)
        listing = self.api.get(self.url + 'folders/proposal/files/')
        self.assertEqual(listing.data['files'][0]['mime_type'], self.item['file']['mimeType'])
        versions = self.api.get(self.file_url + 'versions/')
        self.assertEqual(versions.status_code, 200)
        self.assertEqual([row['is_current'] for row in versions.data['versions']], [True, False])
        self.assertIsNone(versions.data['versions'][1]['modified_by'])
        self.assertEqual(OpportunityAuditEvent.objects.count(), before_audits)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.updated_at, before_updated)

    def test_missing_metadata_remains_unknown_and_no_current_version_is_invented(self):
        for key in ('publication', 'createdBy', 'lastModifiedBy'):
            self.item.pop(key)
        self.item['file'] = {}
        result = self.api.get(self.file_url)
        for key in ('version', 'publication_level', 'created_by', 'modified_by', 'mime_type'):
            self.assertIsNone(result.data[key])
        versions = self.api.get(self.file_url + 'versions/')
        self.assertTrue(all(row['is_current'] is None for row in versions.data['versions']))

    def test_versions_cursor_bound_to_workspace_folder_file_and_expires(self):
        link = self.graph.real.graph_root + self.graph.real.path(self.item['id']) + '/versions?$skiptoken=synthetic'
        self.graph.versions.return_value['@odata.nextLink'] = link
        first = self.api.get(self.file_url + 'versions/')
        cursor = first.data['next_cursor']
        second = self.api.get(self.file_url + 'versions/', {'cursor': cursor})
        self.assertEqual(second.status_code, 200)
        self.graph.versions.assert_called_with(self.item['id'], link)
        another = self.graph.upload(self.folder_id, 'Other.docx', b'bytes')
        other_url = self.url + 'folders/proposal/files/' + another['id'] + '/versions/'
        self.assertEqual(self.api.get(other_url, {'cursor': cursor}).status_code, 400)
        with patch('django.core.signing.time.time', return_value=9999999999):
            self.assertEqual(self.api.get(self.file_url + 'versions/', {'cursor': cursor}).status_code, 400)
        self.assertEqual(self.api.get(self.file_url + 'versions/', {'cursor': 'https://attacker.test'}).status_code, 400)

    def test_each_read_route_denies_missing_read_permission_before_remote_content(self):
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='read').delete()
        for suffix in ('', 'versions/', 'download/'):
            self.assertEqual(self.api.get(self.file_url + suffix).status_code, 403)
        self.graph.versions.assert_not_called()
        self.graph.download.assert_not_called()

    def test_download_requires_export_but_metadata_remains_readable(self):
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        self.assertFalse(self.api.get(self.file_url).data['can_download'])
        self.assertEqual(self.api.get(self.file_url + 'versions/').status_code, 200)
        self.assertEqual(self.api.get(self.file_url + 'download/').status_code, 403)
        self.graph.download.assert_not_called()

    def test_production_guarded_download_checks_export_and_returns_allowed_attachment(self):
        guarded = type('DealViewSet', (ModuleActionGuardMixin, DealViewSet), {'__module__': 'apps.sales.views'})
        callback = guarded.as_view({'get': 'workspace_download'})
        def request_download():
            request = APIRequestFactory().get(self.file_url + 'download/')
            force_authenticate(request, user=self.actor)
            return callback(request, pk=self.opportunity.pk, folder_key='proposal', file_id=self.item['id'])
        allowed = request_download()
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(b''.join(closing_iterator_wrapper(allowed.streaming_content, allowed.close)), b'bytes')
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        self.assertEqual(request_download().status_code, 403)
        self.assertEqual(self.graph.download.call_count, 1)

    def test_out_of_scope_opportunity_never_exposes_file(self):
        hidden = Deal.objects.create(deal_code='HIDDEN', deal_name='Hidden', client=self.client_record, owner=self.other)
        url = self.file_url.replace(str(self.opportunity.pk), str(hidden.pk), 1)
        for suffix in ('', 'versions/', 'download/'):
            self.assertEqual(self.api.get(url + suffix).status_code, 404)
        self.graph.download.assert_not_called()

    def test_foreign_folder_file_drive_or_remote_shortcut_is_rejected(self):
        changes = [
            {'parentReference': {'id': self.workspace.folders['tender']['id'], 'driveId': 'drive-test'}},
            {'parentReference': {'id': self.folder_id, 'driveId': 'other-drive'}},
            {'id': 'unexpected-file'}, {'folder': {}}, {'remoteItem': {'id': 'foreign'}},
            {'webUrl': 'https://attacker.test/private'},
        ]
        original = deepcopy(self.item)
        for change in changes:
            self.graph.items[original['id']] = {**original, **change}
            with self.subTest(change=change):
                for suffix in ('', 'versions/', 'download/'):
                    self.assertEqual(self.api.get(self.file_url + suffix).status_code, 424)
        self.graph.versions.assert_not_called()
        self.graph.download.assert_not_called()

    def test_wrong_workspace_root_is_rejected(self):
        self.graph.items[self.workspace.root_item_id]['parentReference']['id'] = 'different-root'
        for suffix in ('', 'versions/', 'download/'):
            self.assertEqual(self.api.get(self.file_url + suffix).status_code, 424)

    def test_missing_file_and_disabled_connection_return_safe_failure(self):
        original = self.graph.item
        with patch.object(self.graph, 'item', side_effect=lambda key: (_ for _ in ()).throw(WorkspaceError('remote_missing'))
                          if key == self.item['id'] else original(key)):
            self.assertEqual(self.api.get(self.file_url).data['code'], 'remote_missing')
        with override_settings(SALES_WORKSPACE_ENABLED=False):
            self.assertEqual(self.api.get(self.file_url + 'download/').data['code'], 'not_configured')
        self.graph.download.assert_not_called()

    def test_download_attachment_is_authenticated_bounded_and_not_cached(self):
        result = self.api.get(self.file_url + 'download/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(b''.join(result.streaming_content), b'bytes')
        self.assertIn('attachment;', result['Content-Disposition'])
        self.assertIn('Technical Proposal.docx', result['Content-Disposition'])
        self.assertEqual(result['Cache-Control'], 'no-store, private')
        self.assertEqual(result['X-Content-Type-Options'], 'nosniff')
        self.graph.download.assert_called_once_with(self.item['id'], 5, MAX_DOWNLOAD_BYTES)

    def test_large_file_cannot_be_downloaded_but_sharepoint_link_remains(self):
        self.item['size'] = MAX_DOWNLOAD_BYTES + 1
        details = self.api.get(self.file_url)
        self.assertFalse(details.data['can_download'])
        self.assertTrue(details.data['web_url'])
        self.graph.download.side_effect = WorkspaceError('download_too_large')
        self.assertEqual(self.api.get(self.file_url + 'download/').data['code'], 'download_too_large')

    def test_move_change_or_permission_revocation_during_download_closes_private_buffer(self):
        for change in ('move', 'version', 'revoke', 'configuration'):
            with self.subTest(change=change), transaction.atomic():
                original = deepcopy(self.item)
                self.graph.items[self.item['id']] = original
                output = BytesIO(b'bytes')
                def download(*args):
                    if change == 'revoke':
                        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
                    elif change == 'configuration':
                        self.workspace.config_fingerprint = 'changed'
                        self.workspace.save(update_fields=['config_fingerprint'])
                    elif change == 'move':
                        self.graph.items[self.item['id']] = {**original, 'parentReference': {'id': 'moved', 'driveId': 'drive-test'}}
                    else:
                        self.graph.items[self.item['id']] = {**original, 'eTag': 'new-version'}
                    return output
                self.graph.download.side_effect = download
                response = self.api.get(self.file_url + 'download/')
                self.assertIn(response.status_code, (403, 409, 424))
                self.assertFalse(response.streaming)
                self.assertTrue(output.closed)
                transaction.set_rollback(True)

    def test_malformed_version_and_provider_failure_return_safe_error(self):
        self.graph.versions.return_value = {'value': [{'id': []}]}
        self.assertEqual(self.api.get(self.file_url + 'versions/').data['code'], 'invalid_remote_response')
        self.graph.download.side_effect = WorkspaceError('remote_unavailable')
        result = self.api.get(self.file_url + 'download/')
        self.assertEqual(result.status_code, 424)
        self.assertNotIn('synthetic-only', str(result.data))


@override_settings(**CONFIG)
class WorkspaceDocumentTransportTests(SimpleTestCase):
    def setUp(self):
        self.graph = WorkspaceGraph(workspace_config())
        self.graph.token = 'synthetic-bearer'

    @staticmethod
    def response(status=200, headers=None, chunks=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.headers = headers or {}
        response.iter_content.return_value = iter(chunks if chunks is not None else [b'bytes'])
        return response

    def test_download_follows_only_signed_location_without_bearer(self):
        target = 'https://synthetic.sharepoint.com/download?token=synthetic-signed'
        responses = [self.response(302, {'Location': target}), self.response(headers={'Content-Length': '5'})]
        with patch.object(self.graph.session, 'request', side_effect=responses) as request:
            output = self.graph.download('file', 5, MAX_DOWNLOAD_BYTES)
        self.assertEqual(output.read(), b'bytes')
        output.close()
        self.assertEqual(request.call_args_list[0].kwargs['headers']['Authorization'], 'Bearer synthetic-bearer')
        self.assertNotIn('Authorization', request.call_args_list[1].kwargs['headers'])
        self.assertEqual(request.call_args_list[1].args[1], target)
        self.assertTrue(all(not call.kwargs['allow_redirects'] for call in request.call_args_list))

    def test_unsafe_download_urls_never_receive_a_request(self):
        for url in ('http://synthetic.sharepoint.com/file', 'https://attacker.test/file',
                    'https://synthetic.sharepoint.com.attacker.test/file', 'https://files.1drv.com.attacker.test/file',
                    'https://user@synthetic.sharepoint.com/file', 'https://synthetic.sharepoint.com:444/file',
                    'https://synthetic.sharepoint.com:broken/file', 'https://[invalid',
                    'https://synthetic.sharepoint.com/file\nsecret', 'https://synthetic.sharepoint.com/file#fragment'):
            with self.subTest(url=url), patch.object(self.graph.session, 'request', return_value=self.response(302, {'Location': url})) as request:
                with self.assertRaises(WorkspaceError):
                    self.graph.download('file', 5, MAX_DOWNLOAD_BYTES)
                self.assertEqual(request.call_count, 1)

    def test_additional_redirect_size_mismatch_and_late_network_failure_never_return_partial_bytes(self):
        broken = self.response()
        def fail_late():
            yield b'by'
            raise requests.ConnectionError('synthetic-signed-secret')
        broken.iter_content.return_value = fail_late()
        responses = [self.response(302, {'Location': 'https://attacker.test'}),
                     self.response(headers={'Content-Length': '9'}), self.response(chunks=[b'by']),
                     self.response(chunks=[b'bytes-more']), broken]
        for response in responses:
            with self.subTest(response=response), patch.object(self.graph.session, 'request', return_value=response):
                with self.assertRaises(WorkspaceError) as error:
                    self.graph.download('file', 5, MAX_DOWNLOAD_BYTES)
                self.assertNotIn('synthetic-signed-secret', str(error.exception))

    def test_download_size_bound_stops_before_http(self):
        with patch.object(self.graph.session, 'request') as request:
            for size in (None, True, -1, MAX_DOWNLOAD_BYTES + 1):
                with self.subTest(size=size), self.assertRaises(WorkspaceError):
                    self.graph.download('file', size, MAX_DOWNLOAD_BYTES)
            request.assert_not_called()

    def test_signed_one_drive_host_supported_but_second_redirect_is_not_followed(self):
        target = 'https://b0mpua-by3301.files.1drv.com/synthetic-token'
        with patch.object(self.graph.session, 'request', side_effect=[
            self.response(302, {'Location': target}), self.response(),
        ]):
            output = self.graph.download('file', 5, MAX_DOWNLOAD_BYTES)
            self.assertEqual(output.read(), b'bytes')
            output.close()
        with patch.object(self.graph.session, 'request', side_effect=[
            self.response(302, {'Location': target}), self.response(302, {'Location': 'https://attacker.test'}),
        ]) as request:
            with self.assertRaises(WorkspaceError):
                self.graph.download('file', 5, MAX_DOWNLOAD_BYTES)
            self.assertEqual(request.call_count, 2)

    def test_versions_reject_foreign_next_link_and_malformed_pages(self):
        for link in ('https://attacker.test/versions', self.graph.graph_root + self.graph.path('other') + '/versions',
                     self.graph.graph_root + self.graph.path('file') + '/versions#fragment'):
            with self.subTest(link=link), patch.object(self.graph, '_json') as request:
                with self.assertRaises(WorkspaceError):
                    self.graph.versions('file', link)
                request.assert_not_called()
        with patch.object(self.graph, '_json', return_value={'value': [None]}):
            with self.assertRaises(WorkspaceError):
                self.graph.versions('file')

    def test_item_response_must_match_requested_id(self):
        with patch.object(self.graph, '_json', return_value={'id': 'other'}):
            with self.assertRaises(WorkspaceError):
                self.graph.item('file')

    def test_document_routes_have_explicit_action_guards(self):
        for name, expected in (('workspace_file', 'read'), ('workspace_versions', 'read'), ('workspace_download', 'export')):
            view = DealViewSet()
            view.action = name
            self.assertEqual(operation_action(SimpleNamespace(method='GET'), view), expected)
