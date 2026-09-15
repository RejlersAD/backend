"""Project milestone creation and cross-project authorization regressions."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import Project, ProjectMember, ProjectMilestone
from apps.core.project_views import ProjectMilestoneViewSet


class ProjectMilestoneAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='milestone-owner', email='milestone-owner@example.test')
        self.viewer = User.objects.create_user(username='milestone-viewer', email='milestone-viewer@example.test')
        self.engineer = User.objects.create_user(username='milestone-engineer', email='milestone-engineer@example.test')
        self.outsider = User.objects.create_user(username='milestone-outsider', email='milestone-outsider@example.test')
        self.project = Project.objects.create(code='MS-OWN', name='Owned project', owner=self.owner)
        self.other = Project.objects.create(code='MS-OTHER', name='Other project', owner=self.outsider)
        self.owned_other = Project.objects.create(code='MS-OWN-2', name='Second owned project', owner=self.owner)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        ProjectMember.objects.create(project=self.project, user=self.engineer, role='engineer')
        self.milestone = ProjectMilestone.objects.create(
            project=self.project, name='Design release', target_date='2026-10-10',
        )
        self.factory = APIRequestFactory()

    def call(self, user, method, action, payload=None, pk=None, query=''):
        request = getattr(self.factory, method)(f'/projects/milestones/{query}', payload or {}, format='json')
        if user:
            force_authenticate(request, user=user)
        view = ProjectMilestoneViewSet.as_view({method: action})
        return view(request, **({'pk': pk} if pk else {}))

    def test_create_persists_selected_project_and_returns_it(self):
        response = self.call(self.owner, 'post', 'create', {
            'project': self.project.pk, 'name': 'Construction start',
            'description': 'Approved target in the project register.', 'target_date': '2026-11-01',
        })
        self.assertEqual(response.status_code, 201)
        created = ProjectMilestone.objects.get(pk=response.data['id'])
        self.assertEqual(created.project_id, self.project.pk)
        self.assertEqual(response.data['project'], self.project.pk)
        self.assertFalse(created.is_completed)

    def test_create_requires_a_project(self):
        response = self.call(self.owner, 'post', 'create', {'name': 'No project', 'target_date': '2026-11-01'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('project', response.data)

    def test_create_rejects_inaccessible_or_deleted_projects(self):
        for project in [self.other, self.owned_other]:
            if project == self.owned_other:
                project.is_deleted = True
                project.save(update_fields=['is_deleted'])
            with self.subTest(project=project.pk):
                response = self.call(self.owner, 'post', 'create', {
                    'project': project.pk, 'name': 'Forbidden', 'target_date': '2026-11-01',
                })
                self.assertEqual(response.status_code, 400)

    def test_accessible_viewer_can_read_but_cannot_create_update_or_complete(self):
        listed = self.call(self.viewer, 'get', 'list', query=f'?project={self.project.pk}')
        self.assertEqual(listed.status_code, 200)
        for method, action, payload, pk in [
            ('post', 'create', {'project': self.project.pk, 'name': 'Forbidden', 'target_date': '2026-11-01'}, None),
            ('patch', 'partial_update', {'name': 'Forbidden'}, self.milestone.pk),
            ('post', 'mark_completed', {}, self.milestone.pk),
            ('delete', 'destroy', {}, self.milestone.pk),
        ]:
            with self.subTest(action=action):
                self.assertEqual(self.call(self.viewer, method, action, payload, pk).status_code, 403)

    def test_commercial_read_access_does_not_grant_milestone_writes(self):
        with patch('apps.project_control.access.has_commercial_module_access', return_value=True):
            self.assertEqual(self.call(self.outsider, 'get', 'retrieve', pk=self.milestone.pk).status_code, 200)
            self.assertEqual(self.call(self.outsider, 'patch', 'partial_update', {'name': 'Forbidden'}, self.milestone.pk).status_code, 403)
            self.assertEqual(self.call(self.outsider, 'post', 'create', {
                'project': self.project.pk, 'name': 'Forbidden', 'target_date': '2026-11-01',
            }).status_code, 403)

    def test_project_association_is_immutable_even_between_owned_projects(self):
        response = self.call(self.owner, 'patch', 'partial_update', {'project': self.owned_other.pk}, self.milestone.pk)
        self.assertEqual(response.status_code, 400)
        self.milestone.refresh_from_db()
        self.assertEqual(self.milestone.project_id, self.project.pk)

    def test_authorized_member_can_update_real_completion_fields(self):
        response = self.call(self.engineer, 'patch', 'partial_update', {
            'name': 'Design released', 'is_completed': True, 'completed_date': '2026-10-09',
        }, self.milestone.pk)
        self.assertEqual(response.status_code, 200)
        self.milestone.refresh_from_db()
        self.assertTrue(self.milestone.is_completed)
        self.assertEqual(str(self.milestone.completed_date), '2026-10-09')

    def test_unauthenticated_create_is_rejected(self):
        response = self.call(None, 'post', 'create', {
            'project': self.project.pk, 'name': 'Forbidden', 'target_date': '2026-11-01',
        })
        self.assertIn(response.status_code, (401, 403))
