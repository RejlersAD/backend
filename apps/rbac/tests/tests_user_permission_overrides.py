from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.models import AuditLog, Module, Organization, Permission, Role, RolePermission, UserProfile, UserRole
from apps.rbac.permissions import HasPermission, HasModuleAccess
from apps.rbac.views import UserProfileViewSet


class IndividualPermissionTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('permission-admin', email='permission-admin@example.test', is_superuser=True)
        org = Organization.objects.create(name='Permissions test', code='permissions-test')
        self.people = []
        for index in range(2):
            user = get_user_model().objects.create_user(f'permission-user-{index}', email=f'permission-user-{index}@example.test')
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org, 'employee_id': f'up{index}'})
            self.people.append(profile)
        self.role = Role.objects.create(name='Individual test role', code='individual_test', level=3)
        self.module = Module.objects.create(name='Records', code='individual_records')
        self.permissions = {a: Permission.objects.create(module=self.module, name=a, code=f'individual_records.{a}', action=a) for a in ['read', 'create', 'update', 'approve', 'delete', 'export']}
        for profile in self.people:
            UserRole.objects.create(user_profile=profile, role=self.role)
        for permission in self.permissions.values():
            RolePermission.objects.create(role=self.role, permission=permission)

    def request(self, method='get', data=None, actor=None, target=None):
        request = getattr(APIRequestFactory(), method)('/users/permission-overrides/', data or {}, format='json')
        force_authenticate(request, actor or self.actor)
        return UserProfileViewSet.as_view({method: 'permission_overrides'})(request, pk=str((target or self.people[0]).pk))

    def payload(self, effect='deny', action='delete'):
        return {'snapshot': self.request().data['snapshot'], 'reason': 'Limit this user to review duties',
                'changes': [{'permission_id': str(self.permissions[action].pk), 'effect': effect}]}

    def test_deny_only_selected_user_and_restore_inheritance(self):
        memberships = list(UserRole.objects.filter(role=self.role).values())
        baseline = set(self.role.permissions.values_list('id', flat=True))
        # Warm the previous permission read before revoking.
        self.people[0].get_all_permissions()
        self.assertEqual(self.request('patch', self.payload()).status_code, 200)
        self.assertFalse(self.people[0].has_permission(self.permissions['delete'].code))
        self.assertTrue(self.people[1].has_permission(self.permissions['delete'].code))
        self.assertNotIn(self.permissions['delete'], self.people[0].get_all_permissions())
        self.assertEqual(list(UserRole.objects.filter(role=self.role).values()), memberships)
        self.assertSetEqual(set(self.role.permissions.values_list('id', flat=True)), baseline)
        event = AuditLog.objects.get(resource_id=self.people[0].id, metadata__audit_source='user_permission_review')
        self.assertEqual(event.metadata['reason'], 'Limit this user to review duties')
        self.assertEqual(self.request('patch', self.payload('inherit')).status_code, 200)
        self.assertTrue(self.people[0].has_permission(self.permissions['delete'].code))

    def test_allow_only_selected_user(self):
        RolePermission.objects.filter(role=self.role, permission=self.permissions['export']).delete()
        self.assertEqual(self.request('patch', self.payload('allow', 'export')).status_code, 200)
        self.assertTrue(self.people[0].has_permission(self.permissions['export'].code))
        self.assertFalse(self.people[1].has_permission(self.permissions['export'].code))

    def test_deny_wins_over_multiple_roles_and_super_admin_action_bypass(self):
        other = Role.objects.create(name='Other', code='super_admin', level=1)
        UserRole.objects.create(user_profile=self.people[0], role=other)
        self.assertEqual(self.request('patch', self.payload()).status_code, 200)
        request = SimpleNamespace(user=self.people[0].user)
        view = SimpleNamespace(permission_required=self.permissions['delete'].code)
        self.assertFalse(HasPermission().has_permission(request, view))
        self.assertTrue(self.people[0].has_permission(self.permissions['read'].code))

    def test_module_gate_enforces_action_denials_for_superusers(self):
        profile = self.people[0]
        profile.user.is_superuser = True
        profile.user.save()
        self.assertEqual(self.request('patch', self.payload('deny', 'export')).status_code, 200)
        view = SimpleNamespace(module_required=self.module.code, action='export_excel')
        request = SimpleNamespace(user=profile.user, method='GET')
        self.assertFalse(HasModuleAccess().has_permission(request, view))
        view.action = 'list'
        self.assertTrue(HasModuleAccess().has_permission(request, view))

    def test_group_save_changes_all_six_actions_only_for_selected_user(self):
        snapshot = self.request().data['snapshot']
        memberships = list(UserRole.objects.filter(role=self.role).values())
        payload = {'snapshot': snapshot, 'reason': 'Category permissions for this user',
                   'changes': [{'permission_id': str(p.pk), 'effect': 'deny'} for p in self.permissions.values()]}
        response = self.request('patch', payload)
        self.assertEqual(response.status_code, 200)
        for permission in self.permissions.values():
            self.assertFalse(self.people[0].has_permission(permission.code))
            self.assertTrue(self.people[1].has_permission(permission.code))
        self.assertEqual(list(UserRole.objects.filter(role=self.role).values()), memberships)
        payload['snapshot'] = response.data['snapshot']
        payload['changes'][0]['effect'] = 'allow'
        self.assertEqual(self.request('patch', payload).status_code, 200)
        self.assertTrue(self.people[0].has_permission(self.permissions['read'].code))
        self.assertFalse(self.people[0].has_permission(self.permissions['create'].code))

    def test_authorization_and_self_protection(self):
        self.assertEqual(self.request(actor=self.people[1].user).status_code, 403)
        profile = self.people[0]
        profile.user.is_superuser = True
        profile.user.save()
        self.assertEqual(self.request('patch', self.payload(), actor=profile.user).status_code, 403)

    def test_stale_and_invalid_changes_are_atomic(self):
        stale = self.payload()
        self.assertEqual(self.request('patch', self.payload('deny', 'export')).status_code, 200)
        self.assertEqual(self.request('patch', stale).status_code, 409)
        payload = self.payload()
        payload['changes'].append({'permission_id': '00000000-0000-0000-0000-000000000000', 'effect': 'deny'})
        self.assertEqual(self.request('patch', payload).status_code, 400)
        self.assertTrue(self.people[0].has_permission(self.permissions['delete'].code))
        payload = self.payload()
        payload['reason'] = ' '
        self.assertEqual(self.request('patch', payload).status_code, 400)
