"""Canonical links preserve source evidence and reject stale or unauthorized writes."""
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.core.shared_record_models import SharedRecordLinkCommand
from apps.core.shared_record_project import ProjectClientAdapter
from apps.rbac.models import Module, Organization, Permission, UserPermissionOverride, UserProfile
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal
from apps.users.models import User


urlpatterns = [path('api/v1/projects/', include('apps.core.project_urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SharedRecordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='record-admin', email='record-admin@example.test', password='test')
        org, _ = Organization.objects.get_or_create(code='records', defaults={'name': 'Records'})
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        for code in ('project_control', 'sales_clients'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code, 'is_active': True})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.client_record = Client.objects.create(client_code='CLIENT-A', company_name='Example Client',
                                                  industry_type='oil_gas', account_manager=self.user)
        self.project = Project.objects.create(code='PROJECT-A', name='Example Project', client_name='Old source label', owner=self.user)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.url = f'/api/v1/projects/shared-records/project_client/{self.project.pk}/'
        registry = patch('apps.core.shared_records.adapters', return_value={'project_client': ProjectClientAdapter()})
        view_registry = patch('apps.core.shared_record_views.adapters', return_value={'project_client': ProjectClientAdapter()})
        registry.start()
        view_registry.start()
        self.addCleanup(registry.stop)
        self.addCleanup(view_registry.stop)

    def command(self, **changes):
        detail = self.api.get(self.url)
        self.assertEqual(detail.status_code, 200, detail.data)
        return {**{'request_id': str(uuid4()), 'expected_token': detail.data['expected_token'],
                   'reason': 'Reviewed original award evidence', 'targets': {'client_id': str(self.client_record.pk)}}, **changes}

    def test_link_retains_label_and_retry_has_one_audit(self):
        payload = self.command()
        first = self.api.post(self.url + 'link/', payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        retry = self.api.post(self.url + 'link/', payload, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertTrue(retry.data['replayed'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.client_id, self.client_record.pk)
        self.assertEqual(self.project.client_name, 'Old source label')
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 1)
        self.assertEqual(first.data['record']['links']['client']['id'], str(self.client_record.pk))

    def test_stale_source_and_reused_command_content_conflict(self):
        payload = self.command()
        self.project.client_name = 'Source corrected by another editor'
        self.project.save()
        stale = self.api.post(self.url + 'link/', payload, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())
        payload = self.command()
        self.assertEqual(self.api.post(self.url + 'link/', payload, format='json').status_code, 200)
        payload['reason'] = 'Different command content'
        self.assertEqual(self.api.post(self.url + 'link/', payload, format='json').status_code, 409)

    def test_client_read_deny_hides_candidates_and_blocks_link(self):
        payload = self.command()
        for permission in Permission.objects.filter(module__code='sales_clients', action='read'):
            UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission,
                                                   allowed=False)
        response = self.api.get(self.url + 'candidates/?kind=client')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])
        response = self.api.post(self.url + 'link/', payload, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    def test_update_deny_prevents_mapping_even_for_admin(self):
        payload = self.command()
        for permission in Permission.objects.filter(module__code='project_control', action='update'):
            UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission,
                                                   allowed=False)
        response = self.api.post(self.url + 'link/', payload, format='json')
        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.client_id)

    def test_same_name_clients_are_choices_never_auto_linked(self):
        duplicate = Client.objects.create(client_code='CLIENT-B', company_name=self.client_record.company_name,
                                          industry_type='oil_gas', account_manager=self.user)
        candidates = self.api.get(self.url + 'candidates/', {'kind': 'client', 'search': 'Example Client'})
        self.assertEqual({item['id'] for item in candidates.data['results']}, {str(self.client_record.pk), str(duplicate.pk)})
        self.project.refresh_from_db()
        self.assertIsNone(self.project.client_id)

    def test_sales_lineage_rejects_different_client(self):
        other = Client.objects.create(client_code='CLIENT-C', company_name='Other company', industry_type='oil_gas')
        Deal.objects.create(deal_code='OPP-LINK', deal_name='Awarded work', client=other, owner=self.user,
                            converted_project=self.project)
        result = self.api.post(self.url + 'link/', self.command(), format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    def test_invalid_target_and_blank_reason_do_not_mutate(self):
        for changes in ({'targets': {'client_id': 'not-a-uuid'}}, {'reason': '  '}, {'targets': {'employee_id': '1'}}):
            result = self.api.post(self.url + 'link/', self.command(**changes), format='json')
            self.assertIn(result.status_code, (400, 403), result.data)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    def test_link_and_audit_rollback_together(self):
        payload = self.command()
        with patch('apps.core.shared_records.SharedRecordLinkCommand.objects.create', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.api.post(self.url + 'link/', payload, format='json')
        self.project.refresh_from_db()
        self.assertIsNone(self.project.client_id)

    def test_generic_project_edit_cannot_bypass_link_review(self):
        response = self.api.patch(f'/api/v1/projects/{self.project.pk}/',
                                  {'client_id': str(self.client_record.pk)}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.client_id)

    def test_new_project_accepts_canonical_client(self):
        response = self.api.post('/api/v1/projects/', {'code': 'PROJECT-B', 'name': 'New linked project',
                                 'client_id': str(self.client_record.pk)}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = Project.objects.get(code='PROJECT-B')
        self.assertEqual(record.client_id, self.client_record.pk)
        self.assertEqual(record.client_name, self.client_record.company_name)

    def test_foreign_organization_client_rejected(self):
        foreign = User.objects.create_user(username='foreign-link', email='foreign-link@example.test')
        org = Organization.objects.create(code='foreign-links', name='Foreign')
        UserProfile.objects.update_or_create(user=foreign, defaults={'organization': org})
        self.client_record.account_manager = foreign
        self.client_record.save()
        result = self.api.post(self.url + 'link/', self.command(), format='json')
        self.assertEqual(result.status_code, 400, result.data)

    def test_new_project_rejects_client_from_different_owner_organization(self):
        foreign = User.objects.create_user(username='foreign-owner', email='foreign-owner@example.test')
        org = Organization.objects.create(code='foreign-owner-org', name='Foreign owner')
        UserProfile.objects.update_or_create(user=foreign, defaults={'organization': org})
        response = self.api.post('/api/v1/projects/', {'code': 'CROSS-ORG', 'name': 'Invalid link',
                                 'owner_id': foreign.pk, 'client_id': str(self.client_record.pk)}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Project.objects.filter(code='CROSS-ORG').exists())

    def test_queue_and_detail_recheck_source_read(self):
        payload = self.command()
        for permission in Permission.objects.filter(module__code='project_control', action='read'):
            UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission,
                                                   allowed=False)
        self.assertEqual(self.api.get(self.url).status_code, 403)
        self.assertEqual(self.api.get('/api/v1/projects/shared-records/').status_code, 403)
        self.assertEqual(self.api.post(self.url + 'link/', payload, format='json').status_code, 403)

    def test_retry_rechecks_target_visibility(self):
        payload = self.command()
        self.assertEqual(self.api.post(self.url + 'link/', payload, format='json').status_code, 200)
        for permission in Permission.objects.filter(module__code='sales_clients', action='read'):
            UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission, allowed=False)
        self.assertEqual(self.api.post(self.url + 'link/', payload, format='json').status_code, 403)
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 1)

    def test_retry_does_not_confirm_a_subsequently_changed_source(self):
        payload = self.command()
        self.assertEqual(self.api.post(self.url + 'link/', payload, format='json').status_code, 200)
        Project.objects.filter(pk=self.project.pk).update(client_name='Subsequent import correction')
        result = self.api.post(self.url + 'link/', payload, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 1)
