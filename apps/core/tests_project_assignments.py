"""Approval business assignments cannot be manufactured by their beneficiary."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.project_control.access import can_approve_commercial
from apps.project_control.services.epc import setup_epc_project
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/projects/', include('apps.core.project_urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProjectAssignmentSecurityTests(TestCase):
    def setUp(self):
        self.owner, self.engineer, self.other, self.admin = [get_user_model().objects.create_user(
            username=f'assignment-{index}', email=f'assignment-{index}@example.test',
            is_staff=index == 3, is_superuser=index == 3,
        ) for index in range(4)]
        for user in (self.owner, self.engineer, self.other, self.admin):
            grant_approval(user, 'project_control')
            set_position(user)
        role = Role.objects.get(code='test_approve_project_control')
        for action in ('create', 'read', 'update'):
            permission, _ = Permission.objects.get_or_create(
                code=f'project_control.{action}', defaults={
                    'name': action, 'module': role.modules.get(code='project_control'), 'action': action,
                },
            )
            for definition in Permission.objects.filter(module=permission.module, action=action, is_active=True):
                RolePermission.objects.get_or_create(role=role, permission=definition)
        self.project = Project.objects.create(code='ASSIGN-GATES', name='Assignment gates', owner=self.owner)
        for user in (self.engineer, self.other, self.admin):
            ProjectMember.objects.create(project=self.project, user=user, role='engineer')
        self.client = APIClient()
        self.base = f'/api/v1/projects/{self.project.pk}/'

    def test_member_and_administrator_cannot_promote_their_own_project_responsibility(self):
        for actor in (self.engineer, self.admin):
            self.client.force_authenticate(actor)
            response = self.client.post(self.base + 'add_member/', {'user_id': str(actor.pk), 'role': 'project_manager'}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertEqual(ProjectMember.objects.get(project=self.project, user=actor).role, 'engineer')
            self.assertFalse(can_approve_commercial(actor, self.project))

    def test_owner_can_assign_another_employee_and_current_manager_can_manage_others(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.base + 'add_member/', {'user_id': str(self.engineer.pk), 'role': 'project_manager'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(can_approve_commercial(self.engineer, self.project))
        self.client.force_authenticate(self.engineer)
        response = self.client.post(self.base + 'add_member/', {'user_id': str(self.other.pk), 'role': 'project_manager'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_existing_owner_can_only_be_changed_by_other_authorized_person(self):
        for actor in (self.engineer, self.admin):
            self.client.force_authenticate(actor)
            response = self.client.patch(self.base, {'owner_id': str(actor.pk)}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.owner_id, self.owner.pk)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.base, {'owner_id': str(self.other.pk)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.owner_id, self.other.pk)

    def test_epc_setup_cannot_be_used_to_assign_oneself_project_ownership(self):
        values = {'code': self.project.code, 'name': self.project.name, 'client_name': 'Client',
                  'start_date': '2026-01-01', 'end_date': '2026-12-31', 'currency': 'AED', 'scope_type': 'epc'}
        for actor in (self.engineer, self.admin):
            with self.assertRaises(PermissionDenied):
                setup_epc_project(self.project, {**values, 'owner': actor.pk}, user=actor)
        self.project.refresh_from_db()
        self.assertEqual(self.project.owner_id, self.owner.pk)

    def test_ordinary_member_cannot_remove_another_responsibility(self):
        self.client.force_authenticate(self.engineer)
        response = self.client.post(self.base + 'remove_member/', {'user_id': str(self.other.pk)}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(ProjectMember.objects.filter(project=self.project, user=self.other).exists())

    def test_new_project_defaults_to_creating_owner(self):
        self.client.force_authenticate(self.engineer)
        response = self.client.post('/api/v1/projects/', {'code': 'NEW-GATES', 'name': 'New project'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Project.objects.get(code='NEW-GATES').owner_id, self.engineer.pk)
