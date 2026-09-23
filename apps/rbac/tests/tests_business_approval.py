"""Business authority is independent of application administration."""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.hr_core.services import EmployeeService
from apps.rbac.approval_eligibility import (
    approval_access, can_review_profile_document, eligible_approver,
    has_business_position, require_configured_approval,
)
from apps.rbac.models import (
    Module, Organization, Permission, ProfileDocument, Role, RoleModule,
    RolePermission, UserProfile, UserRole, UserPermissionOverride,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [path('api/v1/rbac/', include('apps.rbac.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class BusinessApprovalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Approval policy tests', code='approval-policy-tests')
        self.actor = self.person('reviewer', 'HR Manager')
        self.owner = self.person('submitter', 'Engineer')
        self.admin = self.person('administrator', 'Chief Executive Officer', superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.module = self.grant(self.actor, 'user_mgmt')

    def person(self, name, title, superuser=False):
        user = get_user_model().objects.create_user(name, email=f'{name}@example.test', is_superuser=superuser)
        UserProfile.objects.create(user=user, organization=self.org, job_title=title)
        EmployeeService.create_employee(user=user, employee_number=f'EMP-{name}', employee_code=name,
            first_name=name, last_name='Test', department='Human Resources', designation=title)
        return user

    def grant(self, user, code):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role, _ = Role.objects.get_or_create(code=f'approval-{user.username}', defaults={'name': user.username, 'level': 4})
        UserRole.objects.get_or_create(user_profile=user.rbac_profile, role=role)
        RoleModule.objects.get_or_create(role=role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'update', 'approve']):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        return module

    def document(self, owner=None):
        return ProfileDocument.objects.create(user_profile=(owner or self.owner).rbac_profile,
            document_type='passport', document_file='test-passport.pdf')

    def test_each_of_the_three_gates_is_required(self):
        self.assertTrue(eligible_approver(self.actor, 'user_mgmt', assigned=True, current=True, positions=['hr_manager']))
        self.assertFalse(eligible_approver(self.actor, 'user_mgmt', assigned=False, current=True))
        self.assertFalse(eligible_approver(self.actor, 'user_mgmt', assigned=True, current=False))
        self.assertFalse(eligible_approver(self.owner, 'user_mgmt', assigned=True, current=True))
        self.assertFalse(eligible_approver(self.admin, 'user_mgmt', assigned=True, current=True, positions=['hr_manager']))

    def test_revoked_access_and_stale_profile_take_effect_immediately(self):
        self.assertTrue(approval_access(self.actor, 'user_mgmt'))
        for permission in self.module.permissions.filter(action='approve'):
            UserPermissionOverride.objects.create(user_profile=self.actor.rbac_profile, permission=permission, allowed=False)
        self.assertFalse(approval_access(self.actor, 'user_mgmt'))
        UserPermissionOverride.objects.all().delete()
        UserProfile.objects.filter(user=self.actor).update(status='inactive')
        self.assertFalse(approval_access(self.actor, 'user_mgmt'))

    def test_profile_title_and_obsolete_secondary_title_do_not_confer_authority(self):
        UserProfile.objects.filter(user=self.owner).update(job_title='HR Manager')
        self.assertFalse(has_business_position(self.owner, ['hr_manager']))
        master = self.actor.employee_master
        master.designation = 'Engineer'
        master.job_title_uae = 'HR Manager'
        master.save(update_fields=['designation', 'job_title_uae'])
        self.assertFalse(has_business_position(self.actor, ['hr_manager']))

    def test_profile_document_requires_position_permission_pending_and_other_owner(self):
        document = self.document()
        self.assertTrue(can_review_profile_document(self.actor, document))
        self.assertFalse(can_review_profile_document(self.admin, document))
        self.assertFalse(can_review_profile_document(self.actor, self.document(self.actor)))
        document.verification_status = 'verified'
        self.assertFalse(can_review_profile_document(self.actor, document))

    def test_self_service_cannot_change_approval_identity_even_for_superadmin(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch('/api/v1/rbac/users/me/',
            {'job_title': 'HR Manager', 'department': 'HR', 'first_name': 'Should not change'}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.admin.refresh_from_db()
        self.assertNotEqual(self.admin.first_name, 'Should not change')
        self.assertFalse(has_business_position(self.admin, ['hr_manager']))

    def test_general_self_profile_edit_cannot_change_position(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f'/api/v1/rbac/users/{self.admin.rbac_profile.pk}/',
            {'job_title': 'HR Manager'}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(has_business_position(self.admin, ['hr_manager']))

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={})
    def test_missing_legacy_route_fails_closed_even_for_superadmin(self):
        with self.assertRaises(PermissionDenied):
            require_configured_approval(self.admin, 'user_mgmt', SimpleNamespace(status='pending'), 'approve')

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={
        'user_mgmt.SimpleNamespace.approve': {
            'positions': ['hr_manager'], 'pending_states': ['pending'], 'assignee_field': 'assigned_to_id',
        },
    })
    def test_explicit_route_checks_current_assignment_position_permission_and_stage(self):
        item = SimpleNamespace(status='pending', assigned_to_id=self.actor.pk)
        require_configured_approval(self.actor, 'user_mgmt', item, 'approve')
        item.status = 'approved'
        with self.assertRaises(ValidationError):
            require_configured_approval(self.actor, 'user_mgmt', item, 'approve')
        item.status = 'pending'
        item.assigned_to_id = self.owner.pk
        with self.assertRaises(PermissionDenied):
            require_configured_approval(self.actor, 'user_mgmt', item, 'approve')
        item.assigned_to_id = self.admin.pk
        with self.assertRaises(PermissionDenied):
            require_configured_approval(self.admin, 'user_mgmt', item, 'approve')

    def test_unregistered_approval_handler_fails_before_mutation(self):
        from rest_framework import status, viewsets
        from rest_framework.response import Response
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.rbac.route_guard import ModuleActionGuardMixin
        class Unregistered(ModuleActionGuardMixin, viewsets.ViewSet):
            def approve(self, request):
                raise AssertionError('Unregistered approval must never execute')
        request = APIRequestFactory().post('/api/v1/rbac/users/unsafe-approve/', {})
        force_authenticate(request, self.admin)
        response = Unregistered.as_view({'post': 'approve'})(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_profile_document_api_rejects_admin_then_accepts_assigned_position(self):
        document = self.document()
        url = f'/api/v1/rbac/profile-documents/{document.pk}/verify/'
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.verification_status, 'pending')
        self.client.force_authenticate(self.actor)
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        document.refresh_from_db()
        self.assertEqual(document.verified_by_id, self.actor.pk)
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 403)

    def test_pending_document_queue_returns_serialized_pending_items_to_designated_reviewer(self):
        document = self.document()
        verified = self.document()
        ProfileDocument.objects.filter(pk=verified.pk).update(verification_status='verified')
        inactive = self.document()
        ProfileDocument.objects.filter(pk=inactive.pk).update(is_active=False)
        response = self.client.get(
            '/api/v1/rbac/profile-documents/pending-verification/',
            {'page_size': 100, 'verification_status__in': 'pending'},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        item = response.data['results'][0]
        self.assertEqual(str(item['id']), str(document.pk))
        self.assertEqual(item['user_email'], self.owner.email)
        self.assertTrue(item['can_review'])
        self.assertEqual(item['verification_status'], 'pending')
        count = self.client.get('/api/v1/rbac/profile-documents/pending-verification/?count_only=true')
        self.assertEqual(count.data, {'count': 1})

    def test_pending_document_queue_denies_superadmin_without_designated_business_position(self):
        self.document()
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/v1/rbac/profile-documents/pending-verification/')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn('designated HR or Administration positions', str(response.data['detail']))

    def test_pending_document_queue_denies_reviewer_after_approval_permission_revocation(self):
        for permission in self.module.permissions.filter(action='approve'):
            UserPermissionOverride.objects.create(
                user_profile=self.actor.rbac_profile, permission=permission, allowed=False,
            )
        response = self.client.get('/api/v1/rbac/profile-documents/pending-verification/')
        self.assertEqual(response.status_code, 403, response.data)
