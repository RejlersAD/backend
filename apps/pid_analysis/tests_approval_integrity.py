"""Generic issue edits cannot manufacture the displayed approval decision."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient

from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.route_guard import secure_module_endpoints
from .models import PIDAnalysisReport, PIDDrawing, PIDIssue
from .views import PIDIssueViewSet


urlpatterns = [path('api/v1/pid/issues/<int:pk>/', PIDIssueViewSet.as_view({'patch': 'partial_update'}))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, RADAI_BUSINESS_APPROVAL_ROUTES={})
class PIDApprovalFieldIntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='pid-granted', email='pid-granted@example.test')
        grant_approval(self.user, 'pid_analysis')
        set_position(self.user)
        role = Role.objects.get(code='test_approve_pid_analysis')
        module = role.modules.get(code='pid_analysis')
        Permission.objects.get_or_create(code='pid_analysis.update', defaults={'name': 'Update PID', 'module': module, 'action': 'update'})
        for permission in module.permissions.filter(action='update', is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        drawing = PIDDrawing.objects.create(file='test/no-live-file.pdf', file_size=1, original_filename='test.pdf', uploaded_by=self.user)
        report = PIDAnalysisReport.objects.create(pid_drawing=drawing, report_data={})
        self.issue = PIDIssue.objects.create(report=report, serial_number=1, pid_reference='P-101',
                                            issue_observed='Test', action_required='Review')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_edit_with_approval_grant_cannot_forge_approval_alias(self):
        response = self.client.patch(f'/api/v1/pid/issues/{self.issue.pk}/',
                                     {'approval': 'Approved', 'remark': 'Clarification'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.approval, 'Pending')
        self.assertEqual(self.issue.status, 'pending')
        self.assertEqual(self.issue.remark, 'Clarification')

    def test_generic_status_edit_cannot_bypass_required_business_route(self):
        response = self.client.patch(f'/api/v1/pid/issues/{self.issue.pk}/', {'status': 'approved'}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, 'pending')
