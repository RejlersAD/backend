from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.models import AuditLog, Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.views import RoleViewSet, AuditLogViewSet


class RoleAccessReviewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('access-reviewer', email='reviewer@example.test', is_superuser=True)
        self.organization = Organization.objects.create(name='Review tests', code='review-tests')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': self.organization, 'employee_id': '9001'})
        self.role = Role.objects.create(name='Review target', code='review_target', level=3)
        self.module = Module.objects.create(name='Review application', code='review_application')
        self.permission = Permission.objects.create(module=self.module, name='View records', code='review_application.read', action='read')

    def payload(self, **updates):
        return {
            'module_ids': [str(self.module.id)], 'permission_ids': [str(self.permission.id)],
            'original_module_ids': [], 'original_permission_ids': [],
            'reason': 'Required for the project review team', **updates,
        }

    def submit(self, payload=None, user=None):
        request = APIRequestFactory().post('/roles/review-access/', payload or self.payload(), format='json')
        force_authenticate(request, user or self.user)
        return RoleViewSet.as_view({'post': 'review_access'})(request, pk=str(self.role.pk))

    def test_review_saves_grants_and_reason_in_one_audit_event(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(RoleModule.objects.filter(role=self.role, module=self.module).exists())
        self.assertTrue(RolePermission.objects.filter(role=self.role, permission=self.permission).exists())
        event = AuditLog.objects.get(resource_id=self.role.id, metadata__audit_source='role_access_review')
        self.assertEqual(event.metadata['reason'], self.payload()['reason'])
        self.assertEqual(event.changes['permissions']['before'], [])
        self.assertEqual(event.changes['permissions']['after'], [str(self.permission.id)])
        self.assertEqual(response.data['permissions'][0]['id'], str(self.permission.id))

    def test_action_changes_preserve_assigned_users_and_module_access(self):
        member = get_user_model().objects.create_user('assigned-member')
        profile, _ = UserProfile.objects.get_or_create(user=member, defaults={'organization': self.organization, 'employee_id': '9002'})
        assignment = UserRole.objects.create(user_profile=profile, role=self.role, assigned_by=self.user)
        RoleModule.objects.create(role=self.role, module=self.module)
        RolePermission.objects.create(role=self.role, permission=self.permission)
        actions = [Permission.objects.create(module=self.module, name=action, code=f'review_application.{action}', action=action) for action in ['create', 'update', 'approve', 'delete', 'export']]
        before = list(UserRole.objects.filter(role=self.role).values())
        payload = self.payload(original_module_ids=[str(self.module.id)], original_permission_ids=[str(self.permission.id)], permission_ids=[str(p.id) for p in actions])
        self.assertEqual(self.submit(payload).status_code, 200)
        self.assertEqual(list(UserRole.objects.filter(role=self.role).values()), before)
        self.assertTrue(UserRole.objects.filter(pk=assignment.pk).exists())
        self.assertTrue(RoleModule.objects.filter(role=self.role, module=self.module).exists())
        self.assertSetEqual(set(RolePermission.objects.filter(role=self.role).values_list('permission_id', flat=True)), {p.id for p in actions})

    def test_stale_draft_cannot_overwrite_other_changes(self):
        RoleModule.objects.create(role=self.role, module=self.module)
        self.assertEqual(self.submit().status_code, 409)
        self.assertFalse(RolePermission.objects.filter(role=self.role).exists())

    def test_validation_does_not_partially_change_access(self):
        self.assertEqual(self.submit(self.payload(permission_ids=[str(uuid4())])).status_code, 400)
        self.assertFalse(RoleModule.objects.filter(role=self.role).exists())
        self.assertEqual(self.submit(self.payload(reason='  ')).status_code, 400)

    def test_cannot_change_own_role_or_super_admin(self):
        assignment = UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.assertEqual(self.submit().status_code, 403)
        assignment.delete()
        self.role.code = 'super_admin'
        self.role.save()
        self.assertEqual(self.submit().status_code, 403)

    def test_regular_user_cannot_apply_review(self):
        user = get_user_model().objects.create_user('not-an-admin', email='reader@example.test')
        self.assertEqual(self.submit(user=user).status_code, 403)

    def test_history_filters_to_reviewed_role_changes(self):
        self.submit()
        AuditLog.objects.create(user=self.user, user_email='reviewer@example.test', action='update', resource_type='Role', resource_id=uuid4())
        request = APIRequestFactory().get('/audit-logs/', {'resource_type': 'Role', 'resource_id': str(self.role.id), 'reviewed': 'true'})
        force_authenticate(request, self.user)
        response = AuditLogViewSet.as_view({'get': 'list'})(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
