"""Assigned operational reviewers need approval, not plan-editing permission."""
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import ProjectMember
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.users.models import User
from ..models import OperationalControlReport, OperationalEarningPolicy
from ..operational_control_views import OperationalControlsView
from . import test_operational_controls as operational_fixture
from .test_scheduling_engine import grant_planning_test_actions


# Reproduce the production URL guard exactly, including the original class name
# used by its verified business-approval route registration.
GuardedOperationalControlsView = type(
    OperationalControlsView.__name__,
    (ModuleActionGuardMixin, OperationalControlsView),
    {'__module__': OperationalControlsView.__module__},
)


class OperationalActionGuardTests(TestCase):
    def setUp(self):
        self.ops = operational_fixture.OperationalControlTests(methodName='runTest')
        self.ops.setUp()
        self.reviewer = User.objects.create_user(username='operational-approve-only', email='approve-only@example.test')
        ProjectMember.objects.create(project=self.ops.enterprise, user=self.reviewer, role='project_manager')
        grant_planning_test_actions((self.reviewer,), ('read', 'approve'))
        self.assertTrue(module_action_allowed(self.reviewer, 'planning_package', 'approve'))
        self.assertFalse(module_action_allowed(self.reviewer, 'planning_package', 'create'))
        self.assertFalse(module_action_allowed(self.reviewer, 'planning_package', 'update'))

    def request(self, action=None, *, actor=None, **values):
        factory = APIRequestFactory()
        request = factory.get(self.ops.url, values) if action is None else factory.post(
            self.ops.url, {'action': action, **values}, format='json')
        force_authenticate(request, user=actor or self.reviewer)
        return GuardedOperationalControlsView.as_view()(request, project_id=self.ops.project.pk)

    def test_approve_only_project_manager_can_approve_policy(self):
        state = self.ops.command('create_policy', baseline_id=self.ops.baseline.pk,
            name='Independent measurement basis', definition={'activities': [
                {'activity_id': self.ops.a.pk, 'method': 'manual_percent', 'weight': '1'}]})
        policy = state['policies'][0]
        response = self.request('approve_policy', policy_id=policy['id'], revision=policy['revision'],
                                reason='Approved measurement evidence independently')
        self.assertEqual(response.status_code, 200, response.data)
        saved = OperationalEarningPolicy.objects.get(pk=policy['id'])
        self.assertEqual(saved.status, 'approved')
        self.assertEqual(saved.approved_by_id, self.reviewer.pk)

    def test_approve_only_project_manager_can_publish_submitted_report(self):
        report = self.ops.submitted()
        response = self.request('publish_report', report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Reviewed the weekly observation evidence')
        self.assertEqual(response.status_code, 200, response.data)
        saved = OperationalControlReport.objects.get(pk=report['id'])
        self.assertEqual(saved.status, 'published')
        self.assertEqual(saved.published_by_id, self.reviewer.pk)

    def test_approve_only_project_manager_can_read_but_cannot_edit_or_create(self):
        report = self.ops.report()
        self.assertEqual(self.request(report_id=report['id']).status_code, 200)
        for action, values in [
            ('save_report', {'report_id': report['id'], 'revision': report['revision'], 'observations': []}),
            ('create_policy', {'baseline_id': self.ops.baseline.pk, 'name': 'Forbidden write',
                               'definition': {'activities': []}}),
        ]:
            with self.subTest(action=action):
                response = self.request(action, **values)
                self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(OperationalControlReport.objects.get(pk=report['id']).revision, report['revision'])
        self.assertFalse(OperationalEarningPolicy.objects.filter(name='Forbidden write').exists())

    def test_approve_only_reviewer_can_return_submitted_report(self):
        report = self.ops.submitted()
        response = self.request('return_report', report_id=report['id'], revision=report['revision'],
                                reason='Please clarify the field evidence before publication')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(OperationalControlReport.objects.get(pk=report['id']).status, 'draft')

    def test_update_only_author_can_return_without_approval_permission(self):
        report = self.ops.submitted()
        editor = User.objects.create_user(username='operational-update-only', email='update-only@example.test')
        ProjectMember.objects.create(project=self.ops.enterprise, user=editor, role='project_manager')
        grant_planning_test_actions((editor,), ('read', 'update'))
        self.assertFalse(module_action_allowed(editor, 'planning_package', 'approve'))
        response = self.request('return_report', actor=editor, report_id=report['id'], revision=report['revision'],
                                reason='Reopen for the planner to correct its source evidence')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(OperationalControlReport.objects.get(pk=report['id']).status, 'draft')
