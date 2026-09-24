"""Project sharing uses effective access grants and preserves owner/admin writes."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.route_guard import secure_module_endpoints
from .models import Project, ProjectActivity
from .views import PROJECT_VISIBILITY


urlpatterns = [path('api/v1/project-organizer/', include('apps.project_organizer.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProjectSharingAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.policy = patch.dict(PROJECT_VISIBILITY, {
            'strategy': 'module_team',
            'team_module_codes': ['process_datasheet', 'spec_customization'],
        })
        self.policy.start()
        self.addCleanup(self.policy.stop)
        org = Organization.objects.create(name='Sharing test organization', code='sharing-test')
        users = get_user_model().objects
        self.owner = users.create_user(username='project-owner', email='owner@example.test')
        self.viewer = users.create_user(username='project-viewer', email='viewer@example.test')
        self.profiles = {}
        for user in (self.owner, self.viewer):
            self.profiles[user.pk], _ = UserProfile.objects.get_or_create(
                user=user, defaults={'organization': org},
            )
        self.module = Module.objects.create(name='Sharing Process', code='process_datasheet')
        self.project = Project.objects.create(name='Owner project', created_by=self.owner)
        self.activity = ProjectActivity.objects.create(
            project=self.project, created_by=self.owner, tool_code='hmb_extractor',
            summary='Original activity', metadata={'source': 'synthetic'},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        self.base = '/api/v1/project-organizer/projects/'
        self.detail = f'{self.base}{self.project.pk}/'

    def grant_read(self, user, module=None):
        module = module or self.module
        role, _ = Role.objects.get_or_create(
            code=f'sharing_{module.code}', defaults={'name': f'Sharing {module.code}'},
        )
        RoleModule.objects.get_or_create(role=role, module=module)
        UserRole.objects.get_or_create(user_profile=self.profiles[user.pk], role=role)
        Permission.objects.get_or_create(
            module=module, code=f'{module.code}.read',
            defaults={'name': f'Read {module.code}', 'action': 'read'},
        )
        permissions = list(Permission.objects.filter(module=module, action='read'))
        self.assertTrue(permissions)
        for permission in permissions:
            RolePermission.objects.get_or_create(role=role, permission=permission)
        return permissions

    def grant_both(self):
        self.grant_read(self.owner)
        return self.grant_read(self.viewer)

    def assert_not_shared(self):
        listed = self.client.get(self.base)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn(str(self.project.pk), [item['project_id'] for item in listed.data['items']])
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        self.assertEqual(self.client.get(self.detail + 'activity/').status_code, 403)

    def test_effective_shared_read_grants_allow_project_and_activity_read(self):
        self.grant_both()
        listed = self.client.get(self.base)
        self.assertIn(str(self.project.pk), [item['project_id'] for item in listed.data['items']])
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        activity = self.client.get(self.detail + 'activity/')
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(len(activity.data['items']), 1)

    def test_globally_visible_module_without_action_grant_does_not_share(self):
        self.assertIn(self.module, self.profiles[self.viewer.pk].get_all_modules())
        self.assert_not_shared()

    def test_explicit_read_denial_overrides_shared_role_and_cached_menu(self):
        permissions = self.grant_both()
        self.profiles[self.viewer.pk].get_all_modules()
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        UserPermissionOverride.objects.create(
            user_profile=self.profiles[self.viewer.pk], permission=permissions[0], allowed=False,
        )
        self.assert_not_shared()

    def test_owner_requires_common_effective_read_grant(self):
        self.grant_read(self.viewer)
        self.assert_not_shared()

    def test_different_team_module_grants_do_not_share(self):
        other = Module.objects.create(name='Sharing Specifications', code='spec_customization')
        self.grant_read(self.owner, other)
        self.grant_read(self.viewer)
        self.assert_not_shared()

    def test_alternative_configured_module_can_share(self):
        other = Module.objects.create(name='Sharing Specifications', code='spec_customization')
        self.grant_read(self.owner, other)
        self.grant_read(self.viewer, other)
        self.assertEqual(self.client.get(self.detail).status_code, 200)

    def test_role_revocation_takes_effect_without_menu_cache_expiry(self):
        self.grant_both()
        self.profiles[self.viewer.pk].get_all_modules()
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        UserRole.objects.filter(user_profile=self.profiles[self.viewer.pk]).delete()
        self.assert_not_shared()

    def test_inactive_profile_cannot_gain_shared_access(self):
        self.grant_both()
        profile = self.profiles[self.viewer.pk]
        profile.status = 'inactive'
        profile.save(update_fields=['status'])
        self.assert_not_shared()

    def test_shared_read_does_not_grant_project_or_activity_writes(self):
        self.grant_both()
        self.assertEqual(self.client.patch(self.detail, {'name': 'Changed'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(self.detail).status_code, 403)
        appended = self.client.post(self.detail + 'activity/', {
            'tool_code': 'hmb_extractor', 'summary': 'Unpermitted change',
        }, format='json')
        self.assertEqual(appended.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Owner project')
        self.assertEqual(self.project.activity.count(), 1)

    def test_owner_and_existing_admin_keep_write_access(self):
        for user in (self.owner, self.viewer):
            if user == self.viewer:
                user.is_staff = True
                user.save(update_fields=['is_staff'])
            self.client.force_authenticate(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.patch(self.detail, {'name': 'Allowed'}, format='json').status_code, 200)
                self.assertEqual(self.client.post(self.detail + 'activity/', {
                    'tool_code': 'hmb_extractor', 'summary': 'Allowed activity',
                }, format='json').status_code, 201)

    def test_owner_strategy_does_not_share_even_with_grants(self):
        self.grant_both()
        with patch.dict(PROJECT_VISIBILITY, {'strategy': 'owner'}):
            self.assert_not_shared()

    def test_permission_lookup_failure_does_not_share(self):
        self.grant_both()
        with patch('apps.rbac.action_policy.module_action_allowed', side_effect=RuntimeError('synthetic failure')):
            self.assert_not_shared()
