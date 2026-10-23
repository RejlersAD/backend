from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient
from apps.rbac.route_guard import secure_module_endpoints
from apps.hr_core.models import EmployeeMaster, HRAuditEvent, RecruitmentCandidate
from apps.procurement.tests.approval_fixtures import grant_approval
from apps.rbac.models import Module, Permission, Role, RolePermission, UserPermissionOverride

urlpatterns = [path('api/v1/hr/', include('apps.hr_core.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class TalentAcquisitionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.hr = get_user_model().objects.create_superuser(username='talent-hr', email='hr@example.test', password='test-only')
        self.employee = get_user_model().objects.create_user(username='talent-employee', email='employee@example.test')
        grant_approval(self.hr, 'hr_management')
        grant_approval(self.employee, 'hr_management')
        module = Module.objects.get(code='hr_management')
        for action in ('read', 'create', 'update'):
            permission, _ = Permission.objects.get_or_create(code=f'hr_management.{action}', defaults={'module': module, 'name': action, 'action': action})
            RolePermission.objects.get_or_create(role=Role.objects.get(code='test_approve_hr_management'), permission=permission)
        self.url = '/api/v1/hr/recruitment-candidates/'
        self.payload = dict(name='Example Candidate', email='candidate@example.test', position='Engineer', department='Engineering', status='Applied')

    def test_candidate_persists_and_updates_with_audit_without_creating_employee(self):
        self.client.force_authenticate(self.hr)
        employee_count = EmployeeMaster.objects.count()
        user_count = get_user_model().objects.count()
        created = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['id']
        self.assertEqual(created.data['reference'], f'CAN-{pk:04d}')
        updated = self.client.patch(f'{self.url}{pk}/', {'status': 'Interview', 'interview_notes': 'Technical discussion complete', 'interview_outcome': 'suitable'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        candidate = RecruitmentCandidate.objects.get(pk=pk)
        self.assertEqual(candidate.status, 'Interview')
        self.assertEqual(candidate.interview_outcome, 'suitable')
        self.assertEqual(HRAuditEvent.objects.filter(object_type='recruitment_candidate', object_id=str(pk)).latest('created_at').metadata['interview_outcome'], 'suitable')
        self.assertEqual(len(updated.data['timeline']), 2)
        self.assertEqual(HRAuditEvent.objects.filter(object_type='recruitment_candidate', object_id=str(pk)).count(), 2)
        self.assertEqual(EmployeeMaster.objects.count(), employee_count)
        self.assertEqual(get_user_model().objects.count(), user_count)

    def test_anonymous_and_non_hr_cannot_read_or_write(self):
        for user in (None, self.employee):
            self.client.force_authenticate(user)
            self.assertIn(self.client.get(self.url).status_code, (401, 403))
            self.assertIn(self.client.post(self.url, self.payload, format='json').status_code, (401, 403))
        self.assertFalse(RecruitmentCandidate.objects.exists())

    def test_explicit_action_denial_still_blocks_hr_administrator(self):
        permission = Permission.objects.get(code='hr_management.create')
        UserPermissionOverride.objects.create(user_profile=self.hr.rbac_profile, permission=permission, allowed=False)
        self.client.force_authenticate(self.hr)
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 403)
        self.assertFalse(RecruitmentCandidate.objects.exists())

    def test_normal_hr_role_with_module_grants_can_use_register(self):
        role = Role.objects.create(code='hr_manager', name='HR manager')
        self.employee.rbac_profile.roles.add(role)
        self.client.force_authenticate(self.employee)
        created = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.client.get(self.url).data['count'], 1)

    def test_invalid_stage_email_or_missing_required_fields_are_rejected(self):
        self.client.force_authenticate(self.hr)
        for payload in ({**self.payload, 'status': 'Converted'}, {**self.payload, 'email': 'invalid'}, {'name': 'Incomplete'}):
            self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        self.assertFalse(RecruitmentCandidate.objects.exists())

    def test_explicit_openings_can_be_closed_and_candidates_are_paginated(self):
        self.client.force_authenticate(self.hr)
        opening = self.client.post('/api/v1/hr/recruitment-openings/', {'title': 'Engineer', 'department': 'Engineering'}, format='json')
        self.assertEqual(opening.status_code, 201, opening.data)
        closed = self.client.patch(f"/api/v1/hr/recruitment-openings/{opening.data['id']}/", {'is_open': False}, format='json')
        self.assertEqual(closed.status_code, 200)
        self.assertFalse(closed.data['is_open'])
        self.assertEqual(self.client.get(self.url).data['count'], 0)
        RecruitmentCandidate.objects.bulk_create([RecruitmentCandidate(**{**self.payload, 'email': f'candidate{i}@example.test'}) for i in range(101)])
        first = self.client.get(self.url)
        self.assertEqual(first.data['count'], 101)
        self.assertEqual(len(first.data['results']), 100)
        self.assertIsNotNone(first.data['next'])
        self.assertEqual(len(self.client.get(self.url, {'page': 2}).data['results']), 1)
