"""Synthetic storage transport: no tenant, network share or application DB writes."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.action_policy import operation_action
from apps.rbac.models import RolePermission
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.sales.models import Client, Deal, OpportunityWorkspace, OpportunityWorkspaceUpload
from apps.sales.opportunity_registration import create_registered_opportunity
from apps.sales.opportunity_workspace import (
    FOLDERS, WorkspaceAPIError, _claim, delete_opportunity_with_workspace_guard, due_workspace_ids,
    run_workspace_setup, setup_workspace, upload_workspace_file, workspace_projection,
)
from apps.sales.tests.access_fixtures import grant_sales_actions
from apps.sales.views import DealViewSet
from apps.sales.workspace_graph import WorkspaceError, WorkspaceGraph, workspace_config


CONFIG = {
    'SALES_WORKSPACE_ENABLED': True,
    'SALES_WORKSPACE_TENANT_ID': '11111111-1111-1111-1111-111111111111',
    'SALES_WORKSPACE_CLIENT_ID': '22222222-2222-2222-2222-222222222222',
    'SALES_WORKSPACE_CLIENT_SECRET': 'synthetic-only',
    'SALES_WORKSPACE_HOSTNAME': 'synthetic.sharepoint.com',
    'SALES_WORKSPACE_DRIVE_ID': 'drive-test',
    'SALES_WORKSPACE_ROOT_ITEM_ID': 'opportunities',
    'SALES_WORKSPACE_ROOT_PATH': '/sites/Sales/Documents/Opportunities',
}


class FakeGraph:
    """Retains a synthetic remote tree across repeated worker invocations."""
    def __init__(self, config):
        self.config = config
        self.real = WorkspaceGraph(config)
        self.items = {'opportunities': self.make('opportunities', 'Opportunities', 'parent')}
        self.created = []
        self.failure = None
        self.uploads = 0

    def make(self, item_id, name, parent, folder=True):
        path = self.config.root_path if item_id == 'opportunities' else self.config.root_path + '/' + item_id + '/' + name
        return {'id': item_id, 'name': name, 'webUrl': 'https://' + self.config.hostname + path,
                'parentReference': {'id': parent, 'driveId': self.config.drive_id},
                'size': 5, 'lastModifiedDateTime': '2026-10-01T00:00:00Z',
                **({'folder': {'childCount': 0}} if folder else {'file': {}})}

    def verify_root(self):
        return self.real.validate_item(self.items['opportunities'], name='Opportunities')

    def item(self, item_id):
        return self.items[item_id]

    def validate_item(self, item, **kwargs):
        return self.real.validate_item(item, **kwargs)

    def create_folder(self, parent, name):
        if self.failure and self.failure[0] == name:
            raise WorkspaceError(self.failure[1])
        if any(i['name'] == name and i['parentReference']['id'] == parent for i in self.items.values()):
            raise WorkspaceError('name_conflict')
        item = self.make(str(uuid4()), name, parent)
        self.items[item['id']] = item
        self.created.append(name)
        return item

    def safe_web_url(self, value):
        return self.real.safe_web_url(value)

    def children(self, parent, next_link=None):
        return {'value': [i for i in self.items.values() if i['parentReference']['id'] == parent]}

    def upload(self, parent, name, content):
        self.uploads += 1
        item = self.make(str(uuid4()), name, parent, folder=False)
        item['size'] = len(content)
        self.items[item['id']] = item
        return item


class WorkspaceFixtures:
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='workspace-owner', email='workspace@example.test')
        self.other = get_user_model().objects.create_user(username='workspace-other', email='other@example.test')
        grant_sales_actions(self.actor, 'sales', 'sales_opportunities', 'sales_clients')
        self.client_record = Client.objects.create(client_code='W-CLIENT', company_name='Synthetic Client',
                                                   industry_type='other', account_manager=self.actor)
        self.opportunity = Deal.objects.create(deal_code='Q-102101', deal_name='Synthetic opportunity',
                                               client=self.client_record, owner=self.actor)
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = f'/api/v1/sales/deals/{self.opportunity.pk}/workspace/'
        self.graph = FakeGraph(workspace_config())
        self.graph_patch = patch('apps.sales.opportunity_workspace.WorkspaceGraph', return_value=self.graph)
        self.graph_patch.start()
        self.addCleanup(self.graph_patch.stop)

    def ready(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        self.assertEqual(run_workspace_setup(workspace.pk), {'status': 'ready'})
        workspace.refresh_from_db()
        return workspace


@override_settings(**CONFIG)
class OpportunityWorkspaceTests(WorkspaceFixtures, TestCase):
    def test_initial_get_is_read_only_and_unknown_counts_are_null(self):
        result = self.api.get(self.url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['status'], 'not_created')
        self.assertEqual(len(result.data['folders']), 6)
        self.assertTrue(all(row['item_count'] is None for row in result.data['folders']))
        self.assertFalse(OpportunityWorkspace.objects.exists())
        self.assertFalse(self.graph.created)

    def test_delete_without_storage_evidence_preserves_existing_behavior(self):
        OpportunityWorkspace.objects.create(opportunity=self.opportunity, status='not_configured')
        result = self.api.delete(f'/api/v1/sales/deals/{self.opportunity.pk}/')
        self.assertEqual(result.status_code, 204)
        self.assertFalse(OpportunityWorkspace.objects.exists())

    def test_delete_queued_or_linked_workspace_preserves_recovery_evidence(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        result = self.api.delete(f'/api/v1/sales/deals/{self.opportunity.pk}/')
        self.assertEqual(result.status_code, 409)
        self.assertTrue(OpportunityWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertEqual(run_workspace_setup(workspace.pk)['status'], 'ready')
        self.assertEqual(self.api.delete(f'/api/v1/sales/deals/{self.opportunity.pk}/').status_code, 409)

    def test_client_cascade_cannot_erase_linked_workspace(self):
        workspace = self.ready()
        result = self.api.delete(f'/api/v1/sales/clients/{self.client_record.pk}/')
        self.assertEqual(result.status_code, 409)
        self.assertTrue(OpportunityWorkspace.objects.filter(pk=workspace.pk).exists())
        self.assertTrue(Deal.objects.filter(pk=self.opportunity.pk).exists())

    def test_client_without_external_workspace_preserves_deletion_behavior(self):
        OpportunityWorkspace.objects.create(opportunity=self.opportunity, status='not_configured')
        result = self.api.delete(f'/api/v1/sales/clients/{self.client_record.pk}/')
        self.assertEqual(result.status_code, 204)
        self.assertFalse(OpportunityWorkspace.objects.exists())

    @override_settings(SALES_WORKSPACE_ENABLED=False)
    def test_disabled_does_not_enqueue_or_claim_ready(self):
        result = self.api.get(self.url)
        self.assertEqual(result.data['status'], 'not_configured')
        self.assertFalse(result.data['can_manage'])
        self.assertEqual(self.api.post(self.url + 'setup/', {}, format='json').status_code, 424)
        self.assertEqual(due_workspace_ids(), [])
        self.assertFalse(OpportunityWorkspace.objects.exists())

    def test_registration_outbox_is_atomic_with_number_and_deal(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                opportunity = create_registered_opportunity(actor=self.actor, validated_data={
                    'deal_name': 'Rollback', 'client': self.client_record,
                })
                self.assertEqual(opportunity.document_workspace.status, 'pending')
                raise RuntimeError('synthetic rollback')
        self.assertEqual(Deal.objects.count(), 1)
        self.assertFalse(OpportunityWorkspace.objects.exists())
        self.assertFalse(self.graph.created)

    def test_worker_creates_saved_code_and_six_folders_once(self):
        result = self.api.post(self.url + 'setup/', {}, format='json')
        self.assertEqual(result.status_code, 202)
        workspace = OpportunityWorkspace.objects.get(opportunity=self.opportunity)
        self.assertIn(workspace.pk, due_workspace_ids())
        self.assertFalse(self.graph.created)
        self.assertEqual(run_workspace_setup(workspace.pk), {'status': 'ready'})
        self.assertEqual(self.graph.created, [self.opportunity.deal_code, *[name for _, name, _ in FOLDERS]])
        self.assertEqual(run_workspace_setup(workspace.pk), {'status': 'skipped'})
        self.api.post(self.url + 'setup/', {}, format='json')
        self.assertEqual(len(self.graph.created), 7)
        self.assertTrue(self.api.get(self.url).data['can_upload'])
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.stage, 'lead')

    def test_partial_provider_rejection_retries_only_missing_folders(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        self.graph.failure = ('Proposal', 'remote_access_denied')
        self.assertEqual(run_workspace_setup(workspace.pk)['status'], 'failed')
        workspace.refresh_from_db()
        self.assertEqual(set(workspace.folders), {'correspondence', 'tender'})
        self.graph.failure = None
        setup_workspace(self.opportunity, self.actor)
        self.assertEqual(run_workspace_setup(workspace.pk)['status'], 'ready')
        self.assertEqual(self.graph.created.count('Correspondence'), 1)

    def test_unrelated_existing_root_is_never_adopted(self):
        self.graph.create_folder('opportunities', self.opportunity.deal_code)
        workspace = setup_workspace(self.opportunity, self.actor)
        self.assertEqual(run_workspace_setup(workspace.pk)['code'], 'name_conflict')
        workspace.refresh_from_db()
        self.assertFalse(workspace.root_item_id)
        self.assertEqual(len(self.graph.created), 1)

    def test_uncertain_external_creation_requires_reconciliation(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        self.graph.failure = (self.opportunity.deal_code, 'remote_unavailable')
        self.assertEqual(run_workspace_setup(workspace.pk)['code'], 'recovery_required')
        workspace.refresh_from_db()
        self.assertEqual(workspace.intent['key'], 'root')
        self.assertEqual(self.api.post(self.url + 'setup/', {}, format='json').status_code, 409)
        self.assertFalse(self.graph.created)

    def test_expired_claim_is_reconciled_without_duplicate_creation(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        claimed = _claim(workspace.pk)
        self.assertIsNotNone(claimed)
        self.assertEqual(run_workspace_setup(workspace.pk)['status'], 'skipped')
        OpportunityWorkspace.objects.filter(pk=workspace.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(run_workspace_setup(workspace.pk)['status'], 'ready')

    def test_revoked_requester_stops_worker_before_remote_access(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertEqual(run_workspace_setup(workspace.pk)['code'], 'authority_changed')
        self.assertFalse(self.graph.created)

    def test_destination_change_hides_old_links_and_stops_jobs(self):
        self.ready()
        with override_settings(SALES_WORKSPACE_ROOT_ITEM_ID='different'):
            result = workspace_projection(self.opportunity, self.actor)
            self.assertEqual(result['error_code'], 'configuration_changed')
            self.assertIsNone(result['web_url'])
            self.assertFalse(result['can_upload'])
            with self.assertRaises(WorkspaceAPIError):
                setup_workspace(self.opportunity, self.actor)

    def test_unprivileged_actor_cannot_read_manage_or_upload(self):
        self.ready()
        self.api.force_authenticate(self.other)
        self.assertIn(self.api.get(self.url).status_code, (403, 404))
        self.assertIn(self.api.post(self.url + 'setup/', {}, format='json').status_code, (403, 404))

    def test_explicit_read_permission_required_even_for_setup(self):
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='read').delete()
        self.assertEqual(self.api.get(self.url).status_code, 403)
        self.assertEqual(self.api.post(self.url + 'setup/', {}, format='json').status_code, 403)

    def test_granted_actor_cannot_read_or_mutate_out_of_scope_opportunity(self):
        hidden = Deal.objects.create(deal_code='PRIVATE-VF', deal_name='Hidden commercial data',
                                     client=self.client_record, owner=self.other)
        hidden_url = f'/api/v1/sales/deals/{hidden.pk}/workspace/'
        for suffix, method in [('', 'get'), ('setup/', 'post'), ('folders/tender/files/', 'get')]:
            result = getattr(self.api, method)(hidden_url + suffix)
            self.assertEqual(result.status_code, 404)
            self.assertNotIn('PRIVATE-VF', str(result.data))
        self.assertFalse(OpportunityWorkspace.objects.filter(opportunity=hidden).exists())
        self.assertFalse(self.graph.created)

    def test_upload_requires_each_of_read_create_and_update(self):
        self.ready()
        for action in ('read', 'create', 'update'):
            with self.subTest(action=action), transaction.atomic():
                RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action=action).delete()
                result = self.api.post(self.url + 'folders/tender/upload/', {
                    'upload_request_id': str(uuid4()), 'file': SimpleUploadedFile('scope.pdf', b'bytes'),
                })
                self.assertEqual(result.status_code, 403)
                transaction.set_rollback(True)
        self.assertEqual(self.graph.uploads, 0)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_active_requester_losing_update_grant_stops_worker(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='update').delete()
        self.assertEqual(run_workspace_setup(workspace.pk)['code'], 'authority_changed')
        self.assertFalse(self.graph.created)

    def test_configuration_change_after_root_checkpoint_stops_further_creation(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        original_create = self.graph.create_folder
        changed = override_settings(SALES_WORKSPACE_ROOT_ITEM_ID='new-container')
        def create_then_reconfigure(parent, name):
            item = original_create(parent, name)
            changed.enable()
            self.addCleanup(changed.disable)
            return item
        with patch.object(self.graph, 'create_folder', side_effect=create_then_reconfigure):
            self.assertEqual(run_workspace_setup(workspace.pk)['code'], 'configuration_changed')
        workspace.refresh_from_db()
        self.assertTrue(workspace.root_item_id)
        self.assertFalse(workspace.folders)
        self.assertEqual(len(self.graph.created), 1)

    def test_stale_worker_token_cannot_checkpoint_external_result(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        original_create = self.graph.create_folder
        replacement_token = uuid4()
        def create_then_lose_lease(parent, name):
            item = original_create(parent, name)
            OpportunityWorkspace.objects.filter(pk=workspace.pk).update(lease_token=replacement_token)
            return item
        with patch.object(self.graph, 'create_folder', side_effect=create_then_lose_lease):
            self.assertEqual(run_workspace_setup(workspace.pk)['status'], 'superseded')
        workspace.refresh_from_db()
        self.assertEqual(workspace.lease_token, replacement_token)
        self.assertTrue(workspace.intent)
        self.assertFalse(workspace.root_item_id)

    def test_broker_failure_keeps_durable_job_due(self):
        from apps.sales.tasks import dispatch_opportunity_workspaces
        workspace = setup_workspace(self.opportunity, self.actor)
        with patch('apps.sales.tasks.provision_opportunity_workspace.delay', side_effect=RuntimeError('broker down')):
            self.assertEqual(dispatch_opportunity_workspaces(), {'dispatched': 0})
        self.assertIn(workspace.pk, due_workspace_ids())

    def test_ready_get_verifies_six_folders_and_live_counts_without_db_writes(self):
        workspace = self.ready()
        before = workspace.updated_at
        self.graph.items[workspace.folders['tender']['id']]['folder']['childCount'] = 3
        result = self.api.get(self.url)
        self.assertEqual(result.data['status'], 'ready')
        self.assertEqual(next(f for f in result.data['folders'] if f['key'] == 'tender')['item_count'], 3)
        workspace.refresh_from_db()
        self.assertEqual(workspace.updated_at, before)
        del self.graph.items[workspace.folders['internal']['id']]
        result = self.api.get(self.url)
        self.assertEqual(result.data['status'], 'failed')
        self.assertFalse(result.data['can_upload'])
        self.assertIsNone(result.data['web_url'])
        self.assertTrue(all(row['item_count'] is None and row['web_url'] is None for row in result.data['folders']))

    def test_listing_verifies_parent_chain_and_projects_files(self):
        workspace = self.ready()
        folder_id = workspace.folders['tender']['id']
        item = self.graph.upload(folder_id, 'scope.pdf', b'bytes')
        result = self.api.get(self.url + 'folders/tender/files/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['files'][0]['id'], item['id'])
        self.graph.items[folder_id]['parentReference']['id'] = 'different-opportunity'
        self.assertEqual(self.api.get(self.url + 'folders/tender/files/').status_code, 424)

    def test_existing_long_sharepoint_filename_can_be_listed(self):
        workspace = self.ready()
        name = 'x' * 210 + '.pdf'
        self.graph.upload(workspace.folders['tender']['id'], name, b'bytes')
        result = self.api.get(self.url + 'folders/tender/files/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['files'][0]['name'], name)

    def test_forged_listing_cursor_never_reaches_remote_url(self):
        self.ready()
        self.assertEqual(self.api.get(self.url + 'folders/tender/files/?cursor=https://example.test').status_code, 400)

    def test_upload_is_idempotent_and_does_not_change_award_or_proposal(self):
        workspace = self.ready()
        request_id = uuid4()
        def upload(content=b'bytes'):
            return self.api.post(self.url + 'folders/award/upload/', {
                'upload_request_id': str(request_id), 'file': SimpleUploadedFile('award.pdf', content),
            })
        self.assertEqual(upload().status_code, 201)
        self.assertEqual(upload().status_code, 200)
        self.assertEqual(upload(b'changed').status_code, 409)
        self.assertEqual(self.graph.uploads, 1)
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.stage, 'lead')
        self.assertEqual(OpportunityWorkspaceUpload.objects.filter(workspace=workspace).count(), 1)

    def test_upload_failure_retains_uncertain_identity_and_safe_error(self):
        self.ready()
        request_id = uuid4()
        with patch.object(self.graph, 'upload', side_effect=RuntimeError('synthetic-secret-and-body')):
            result = self.api.post(self.url + 'folders/tender/upload/', {
                'upload_request_id': str(request_id), 'file': SimpleUploadedFile('scope.pdf', b'bytes'),
            })
        self.assertEqual(result.status_code, 424)
        self.assertNotIn('synthetic-secret', str(result.data))
        attempt = OpportunityWorkspaceUpload.objects.get(request_id=request_id)
        self.assertEqual(attempt.status, 'uncertain')
        with self.assertRaises(WorkspaceAPIError):
            upload_workspace_file(self.opportunity, self.actor, 'tender', SimpleUploadedFile('scope.pdf', b'bytes'), request_id)

    @override_settings(SALES_WORKSPACE_MAX_UPLOAD_BYTES=4)
    def test_upload_validates_limit_before_network_or_attempt(self):
        self.ready()
        result = self.api.post(self.url + 'folders/tender/upload/', {
            'upload_request_id': str(uuid4()), 'file': SimpleUploadedFile('scope.pdf', b'12345'),
        })
        self.assertEqual(result.status_code, 400)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_setup_does_not_accept_arbitrary_destinations(self):
        self.assertEqual(self.api.post(self.url + 'setup/', {'root': 'https://other.test/'}, format='json').status_code, 400)
        self.assertFalse(OpportunityWorkspace.objects.exists())


@override_settings(**CONFIG)
class WorkspaceGraphTests(SimpleTestCase):
    def test_configuration_off_or_incomplete_is_unavailable(self):
        with override_settings(SALES_WORKSPACE_ENABLED=False):
            self.assertIsNone(workspace_config())
        with override_settings(SALES_WORKSPACE_CLIENT_SECRET=''):
            self.assertIsNone(workspace_config())
        with override_settings(SALES_WORKSPACE_HOSTNAME='synthetic.sharepoint.com.attacker.test'):
            self.assertIsNone(workspace_config())
        with override_settings(SALES_WORKSPACE_ROOT_PATH='/sites/Sales/Documents/../Opportunities'):
            self.assertIsNone(workspace_config())

    def test_external_or_wrong_parent_response_is_rejected(self):
        graph = WorkspaceGraph(workspace_config())
        for url in ('https://attacker.test/file', 'https://synthetic.sharepoint.com/sites/Other/x'):
            with self.assertRaises(WorkspaceError):
                graph.safe_web_url(url)

    def test_malformed_remote_metadata_raises_safe_transport_error(self):
        graph = WorkspaceGraph(workspace_config())
        for url in ('https://synthetic.sharepoint.com:broken/path', 'https://[invalid',
                    'https://synthetic.sharepoint.com/sites/Sales/Documents/Opportunities/%2e%2e/Other'):
            with self.subTest(url=url), self.assertRaises(WorkspaceError):
                graph.safe_web_url(url)
        for item in (None, [], {'id': 'id', 'parentReference': None}, {'id': 'id', 'parentReference': {}, 'folder': None}):
            with self.subTest(item=item), self.assertRaises(WorkspaceError):
                graph.validate_item(item)

    def test_malformed_remote_upload_session_is_rejected(self):
        graph = WorkspaceGraph(workspace_config())
        with patch.object(graph, '_json', return_value={'uploadUrl': 'https://synthetic.sharepoint.com:invalid/upload'}):
            with self.assertRaises(WorkspaceError):
                graph.upload('parent', 'scope.pdf', b'bytes')

    def test_http_adapter_redacts_provider_payload_and_disallows_redirects(self):
        graph = WorkspaceGraph(workspace_config())
        graph.token = 'synthetic-token'
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 403
        response.text = 'synthetic-provider-secret'
        with patch.object(graph.session, 'request', return_value=response) as request:
            with self.assertRaises(WorkspaceError) as captured:
                graph.item('item')
        self.assertEqual(str(captured.exception), 'remote_access_denied')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
        self.assertEqual(request.call_args.kwargs['timeout'], (5, 25))

    def test_folder_create_uses_fail_conflict(self):
        graph = WorkspaceGraph(workspace_config())
        item = FakeGraph(graph.config).make('new', 'Tender', 'parent')
        with patch.object(graph, '_json', return_value=item) as request:
            graph.create_folder('parent', 'Tender')
        self.assertEqual(request.call_args.kwargs['json']['@microsoft.graph.conflictBehavior'], 'fail')

    def test_upload_session_fails_conflict_and_never_sends_bearer_to_upload_url(self):
        graph = WorkspaceGraph(workspace_config())
        item = FakeGraph(graph.config).make('new', 'scope.pdf', 'parent', folder=False)
        with patch.object(graph, '_json', side_effect=[{
            'uploadUrl': 'https://synthetic.sharepoint.com/upload/token',
        }, item]) as request:
            graph.upload('parent', 'scope.pdf', b'bytes')
        self.assertEqual(request.call_args_list[0].kwargs['json']['item']['@microsoft.graph.conflictBehavior'], 'fail')
        self.assertFalse(request.call_args_list[1].kwargs['authenticated'])

    def test_untrusted_upload_and_next_page_urls_are_rejected(self):
        graph = WorkspaceGraph(workspace_config())
        with patch.object(graph, '_json', return_value={'uploadUrl': 'https://attacker.test/upload'}):
            with self.assertRaises(WorkspaceError):
                graph.upload('parent', 'scope.pdf', b'bytes')
        with self.assertRaises(WorkspaceError):
            graph.children('parent', 'https://graph.microsoft.com/v1.0/drives/other/items/other/children')

    def test_workspace_custom_commands_have_explicit_guard_policies(self):
        for name, verb, expected in [('workspace', 'GET', 'read'), ('workspace_files', 'GET', 'read'),
                                     ('workspace_setup', 'POST', 'update'), ('workspace_upload', 'POST', 'create')]:
            view = DealViewSet()
            view.action = name
            self.assertEqual(operation_action(SimpleNamespace(method=verb), view), expected)


@override_settings(**CONFIG)
class WorkspaceGuardDurabilityTests(WorkspaceFixtures, TransactionTestCase):
    """Exercise the actual guard wrapper without TestCase's enclosing transaction."""
    def test_guarded_upload_commits_attempt_before_network_and_retains_failure(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        self.ready()
        guarded = type('DealViewSet', (ModuleActionGuardMixin, DealViewSet), {'__module__': 'apps.sales.views'})
        callback = guarded.as_view({'post': 'workspace_upload'})
        request = APIRequestFactory().post(self.url + 'folders/tender/upload/', {
            'upload_request_id': str(uuid4()), 'file': SimpleUploadedFile('scope.pdf', b'bytes'),
        })
        force_authenticate(request, user=self.actor)
        def fail_remote(*args):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
            raise WorkspaceError('remote_unavailable')
        with patch.object(self.graph, 'upload', side_effect=fail_remote):
            response = callback(request, pk=self.opportunity.pk, folder_key='tender')
        self.assertEqual(response.status_code, 424)
        self.assertEqual(OpportunityWorkspaceUpload.objects.get().status, 'uncertain')

    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks.')
    def test_concurrent_workers_claim_one_live_lease(self):
        workspace = setup_workspace(self.opportunity, self.actor)
        barrier = Barrier(2)
        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                result = _claim(workspace.pk)
                return str(result[2]) if result else None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)
        workspace.refresh_from_db()
        self.assertIn(str(workspace.lease_token), results)

    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks.')
    def test_concurrent_setup_requests_share_one_workspace(self):
        barrier = Barrier(2)
        def setup():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                opportunity = Deal.objects.get(pk=self.opportunity.pk)
                barrier.wait(timeout=10)
                return str(setup_workspace(opportunity, actor).pk)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: setup(), range(2)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(OpportunityWorkspace.objects.count(), 1)

    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL FK and row locks.')
    def test_delete_waiting_for_workspace_does_not_deadlock_worker_audit(self):
        from apps.sales.workflow import _audit
        workspace = setup_workspace(self.opportunity, self.actor)
        workspace_locked, deal_locked = Event(), Event()
        def checkpoint():
            close_old_connections()
            try:
                with transaction.atomic():
                    OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
                    workspace_locked.set()
                    self.assertTrue(deal_locked.wait(10))
                    _audit(Deal.objects.get(pk=self.opportunity.pk), get_user_model().objects.get(pk=self.actor.pk),
                           'workspace_failed', data={'code': 'synthetic_check'})
                return 'audit_committed'
            finally:
                close_old_connections()
        def deletion():
            close_old_connections()
            try:
                self.assertTrue(workspace_locked.wait(10))
                def observe_parent_lock(execute, sql, params, many, context):
                    result = execute(sql, params, many, context)
                    if 'sales_deals' in sql and ('FOR UPDATE' in sql or 'FOR NO KEY UPDATE' in sql):
                        deal_locked.set()
                    return result
                with connection.execute_wrapper(observe_parent_lock):
                    try:
                        delete_opportunity_with_workspace_guard(Deal.objects.get(pk=self.opportunity.pk))
                    except WorkspaceAPIError as exc:
                        return exc.status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            audit_future = pool.submit(checkpoint)
            deletion_future = pool.submit(deletion)
            self.assertEqual(audit_future.result(timeout=20), 'audit_committed')
            self.assertEqual(deletion_future.result(timeout=20), 409)
        self.assertTrue(Deal.objects.filter(pk=self.opportunity.pk).exists())
