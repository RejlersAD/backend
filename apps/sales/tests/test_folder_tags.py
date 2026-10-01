"""Custom logical-folder metadata, without provisioning or external writes."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import RolePermission
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal, OpportunityFolderTag, OpportunityWorkspace, OpportunityAuditEvent
from apps.sales.views import DealViewSet
from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('deals', DealViewSet, basename='folder-tag-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


class FolderTagFixtures:
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user(username='folder-tag-owner', email='folder-tag@example.test')
        grant_sales_actions(self.actor, 'sales', 'sales_opportunities')
        self.customer = Client.objects.create(client_code='TAG-CLIENT', company_name='Synthetic Client',
                                              industry_type='other', account_manager=self.actor)
        self.deal = Deal.objects.create(deal_code='Q-TAG-1', deal_name='Synthetic opportunity',
                                       client=self.customer, owner=self.actor)
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = f'/api/v1/sales/deals/{self.deal.pk}/workspace/'

    def payload(self, tag='Reviewed scope', key='tender'):
        state = self.api.get(self.url)
        self.assertEqual(state.status_code, 200, state.data)
        row = next(folder for folder in state.data['folders'] if folder['key'] == key)
        return {'tag': tag, 'expected_token': row['tag_token']}

    def save(self, data=None, key='tender'):
        return self.api.patch(self.url + f'folders/{key}/tag/', self.payload(key=key) if data is None else data, format='json')


@override_settings(ROOT_URLCONF=__name__, SALES_WORKSPACE_ENABLED=False)
class OpportunityFolderTagTests(FolderTagFixtures, TestCase):
    def test_initial_read_returns_empty_tags_tokens_and_never_creates_workspace_or_rows(self):
        response = self.api.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertTrue(response.data['can_edit_tags'])
        self.assertEqual(len(response.data['folders']), 6)
        self.assertTrue(all(folder['tag'] == '' and folder['tag_token'] for folder in response.data['folders']))
        self.assertFalse(OpportunityWorkspace.objects.exists())
        self.assertFalse(OpportunityFolderTag.objects.exists())

    def test_save_clear_and_reload_are_shared_across_storage_providers(self):
        original = {'stage': self.deal.stage, 'bid_decision': self.deal.bid_decision, 'updated_at': self.deal.updated_at}
        payload = self.payload('  Engineering review  ')
        with patch('apps.sales.opportunity_workspace.WorkspaceGraph', side_effect=AssertionError('No remote I/O')):
            result = self.save(payload)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['tag'], 'Engineering review')
        self.assertFalse(result.data['replayed'])
        saved = OpportunityFolderTag.objects.get()
        self.assertEqual(saved.revision, 1)
        self.assertEqual(saved.updated_by, self.actor)
        for storage in ('radai', 'sharepoint'):
            response = self.api.get(self.url, {'storage': storage})
            row = next(folder for folder in response.data['folders'] if folder['key'] == 'tender')
            self.assertEqual(row['tag'], 'Engineering review')
        cleared = self.save({'tag': '', 'expected_token': result.data['expected_token']})
        self.assertEqual(cleared.status_code, 200, cleared.data)
        saved.refresh_from_db()
        self.assertEqual(saved.tag, '')
        self.assertEqual(saved.revision, 2)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_folder_tag_changed').count(), 2)
        self.assertFalse(OpportunityWorkspace.objects.exists())
        self.deal.refresh_from_db()
        self.assertEqual({key: getattr(self.deal, key) for key in original}, original)

    def test_exact_latest_retry_has_one_effect_and_different_stale_value_conflicts(self):
        payload = self.payload()
        self.assertEqual(self.save(payload).status_code, 200)
        replay = self.save(payload)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data['replayed'])
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
        stale = self.save({**payload, 'tag': 'Competing tag'})
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertEqual(OpportunityFolderTag.objects.get().tag, payload['tag'])
        self.save(self.payload('Newer value'))
        self.assertEqual(self.save(payload).status_code, 409)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 2)

    def test_empty_unchanged_tag_is_noop_and_other_folder_edit_keeps_token_valid(self):
        untouched = self.payload('', key='award')
        no_op = self.save(untouched, key='award')
        self.assertEqual(no_op.status_code, 200, no_op.data)
        self.assertFalse(OpportunityFolderTag.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.exists())
        tender = self.payload()
        self.assertEqual(self.save(self.payload('Award notes', key='award'), key='award').status_code, 200)
        self.assertEqual(self.save(tender).status_code, 200)
        self.assertEqual(OpportunityFolderTag.objects.count(), 2)

    def test_current_read_update_required_but_create_export_not_required(self):
        RolePermission.objects.filter(permission__module__code='sales_opportunities',
                                      permission__action__in=['create', 'export']).delete()
        payload = self.payload()
        self.assertEqual(self.save(payload).status_code, 200)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='update').delete()
        self.assertFalse(self.api.get(self.url).data['can_edit_tags'])
        self.assertEqual(self.save(payload).status_code, 403)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='read').delete()
        self.assertEqual(self.api.get(self.url).status_code, 403)
        self.assertEqual(self.save(payload).status_code, 403)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_token_is_bound_to_actor_opportunity_and_category(self):
        payload = self.payload()
        self.assertEqual(self.save(payload, key='award').status_code, 409)
        other_deal = Deal.objects.create(deal_code='Q-TAG-2', deal_name='Other opportunity', client=self.customer, owner=self.actor)
        response = self.api.patch(f'/api/v1/sales/deals/{other_deal.pk}/workspace/folders/tender/tag/', payload, format='json')
        self.assertEqual(response.status_code, 409)
        other_actor = get_user_model().objects.create_user(username='other-tag-reviewer', email='other-tag@example.test')
        grant_sales_actions(other_actor, 'sales', 'sales_opportunities')
        self.api.force_authenticate(other_actor)
        self.assertEqual(self.save(payload).status_code, 409)
        self.assertFalse(OpportunityFolderTag.objects.exists())

    def test_read_denial_with_update_grant_blocks_save_and_replay(self):
        payload = self.payload()
        self.assertEqual(self.save(payload).status_code, 200)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='read').delete()
        self.assertEqual(self.save(payload).status_code, 403)
        self.assertEqual(OpportunityFolderTag.objects.get().revision, 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_foreign_opportunity_is_not_accessible(self):
        outsider = get_user_model().objects.create_user(username='private-tag-owner', email='private-tag@example.test')
        private = Deal.objects.create(deal_code='PRIVATE-TAG', deal_name='Private opportunity', client=self.customer, owner=outsider)
        result = self.api.patch(f'/api/v1/sales/deals/{private.pk}/workspace/folders/tender/tag/', self.payload(), format='json')
        self.assertEqual(result.status_code, 404)
        self.assertFalse(OpportunityFolderTag.objects.exists())

    def test_unknown_folder_and_invalid_tag_or_payload_are_rejected(self):
        payload = self.payload()
        for key in ('null', 'undefined', 'other', 'Tender'):
            self.assertEqual(self.save(payload, key=key).status_code, 400)
        for tag in (None, True, 4, ['label'], {'text': 'label'}, 'x' * 65, 'line\nbreak', 'nul\x00byte'):
            self.assertEqual(self.save({**payload, 'tag': tag}).status_code, 400)
        self.assertEqual(self.save({**payload, 'provider': 'sharepoint'}).status_code, 400)
        self.assertEqual(self.save({'tag': 'Missing token'}).status_code, 400)
        self.assertEqual(self.save({**payload, 'expected_token': 'invalid'}).status_code, 409)
        self.assertFalse(OpportunityFolderTag.objects.exists())

    def test_audit_failure_rolls_back_insert_and_update(self):
        payload = self.payload()
        with patch('apps.sales.folder_tags._audit', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.save(payload)
        self.assertFalse(OpportunityFolderTag.objects.exists())
        self.assertEqual(self.save(payload).status_code, 200)
        row = OpportunityFolderTag.objects.get()
        changed = self.payload('New text')
        with patch('apps.sales.folder_tags._audit', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.save(changed)
        row.refresh_from_db()
        self.assertEqual(row.tag, payload['tag'])
        self.assertEqual(row.revision, 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)
