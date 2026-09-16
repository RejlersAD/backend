"""The reporting-manager approval route cannot be changed by swapping the subject."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient

from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.route_guard import secure_module_endpoints
from .models import ClientSite, SiteVisitRequest
from .views import SiteVisitRequestViewSet


urlpatterns = [path('api/v1/site-visits/requests/<uuid:pk>/', SiteVisitRequestViewSet.as_view({'patch': 'partial_update'}))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SiteVisitSubjectIntegrityTests(TestCase):
    def setUp(self):
        self.employee, self.manager, self.other, self.admin = [get_user_model().objects.create_user(
            username=f'site-subject-{index}', email=f'site-subject-{index}@example.test', is_superuser=index == 3,
        ) for index in range(4)]
        for user in (self.employee, self.manager, self.other, self.admin):
            grant_approval(user, 'timesheet')
            set_position(user)
        employee = self.employee.employee_master
        employee.manager = self.manager.employee_master
        employee.save(update_fields=['manager'])
        role = Role.objects.get(code='test_approve_timesheet')
        module = role.modules.get(code='timesheet')
        Permission.objects.get_or_create(code='timesheet.update', defaults={'name': 'Update timesheet', 'module': module, 'action': 'update'})
        for permission in module.permissions.filter(action='update', is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        site = ClientSite.objects.create(name='Test site', client_name='Test client', address='Test address')
        self.visit = SiteVisitRequest.objects.create(employee=self.employee, employee_name='Employee', employee_code='EMP-1',
            department='Engineering', site=site, start_date=date(2026, 10, 1), end_date=date(2026, 10, 2), purpose='Inspect')
        self.client = APIClient()
        self.url = f'/api/v1/site-visits/requests/{self.visit.pk}/'

    def test_employee_and_manager_cannot_swap_request_subject(self):
        for actor in (self.employee, self.manager):
            self.client.force_authenticate(actor)
            response = self.client.patch(self.url, {'employee': str(self.other.pk)}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.employee_id, self.employee.pk)

    def test_unassigned_employee_or_superuser_cannot_edit_request(self):
        for actor in (self.other, self.admin):
            self.client.force_authenticate(actor)
            response = self.client.patch(self.url, {'purpose': 'Changed'}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.purpose, 'Inspect')

    def test_pending_owner_edit_preserves_identity_and_decision_status(self):
        self.client.force_authenticate(self.employee)
        response = self.client.patch(self.url, {'purpose': 'Updated inspection note'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, 'PENDING')
        self.assertEqual(self.visit.employee_id, self.employee.pk)

    def test_approved_request_cannot_be_changed_by_owner_or_manager(self):
        SiteVisitRequest.objects.filter(pk=self.visit.pk).update(status='APPROVED', approved_by=self.manager)
        for actor in (self.employee, self.manager):
            self.client.force_authenticate(actor)
            response = self.client.patch(self.url, {'purpose': 'Change approved scope'}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.purpose, 'Inspect')
