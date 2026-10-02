"""Canonical links preserve source evidence and existing domain authority."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.core.shared_records import LinkConflict, link_record, version_token
from apps.core.shared_record_models import SharedRecordLinkCommand
from apps.hr_core.models import EmployeeMaster
from apps.project_control.models import ApprovedHourEntry, ControlAccount, ReportingPeriod, WBSNode
from apps.project_control.serializers import ApprovedHourEntrySerializer
from apps.project_control.shared_record_links import ADAPTERS as CONTROL_ADAPTERS
from apps.project_organizer.models import Project as OrganizerProject
from apps.project_organizer.shared_record_links import ADAPTERS as ORGANIZER_ADAPTERS
from apps.project_organizer.views import _sanitize_payload, ALLOWED_UPDATE_FIELDS
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..models import ActivityAssignment, PlanningProject, Schedule, ScheduleActivity, ScheduleResource, ScheduleVersion
from ..schedule_serializers import ScheduleResourceSerializer
from ..serializers import PlanningProjectSerializer
from ..shared_record_links import ADAPTERS
from ..views import PlanningProjectViewSet


urlpatterns = [
    path('api/v1/projects/', include('apps.core.project_urls')),
    path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls')),
    path('api/v1/project-control/', include('apps.project_control.urls')),
    path('api/v1/project-organizer/', include('apps.project_organizer.urls')),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SharedResourceIdentityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(code='identity-resources', name='Identity resources')
        self.owner = self.user('identity-owner')
        self.worker = self.user('identity-worker')
        self.employee = self.employee_for(self.worker)
        self.grant(self.owner, 'project_control', 'planning_package')
        self.project = Project.objects.create(code='ID-RESOURCE', name='Identity project', owner=self.owner)
        self.workspace = PlanningProject.objects.create(name='Legacy workspace', client='Legacy client', created_by=self.owner)
        self.context = {'request': SimpleNamespace(user=self.owner)}
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def user(self, name, organization=None):
        user = User.objects.create_user(name, email=f'{name}@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization or self.org})
        profile.organization = organization or self.org
        profile.save(update_fields=['organization'])
        return user

    def employee_for(self, user):
        return EmployeeMaster.objects.create(user=user, employee_number=f'RES-{user.pk}',
            employee_code=f'RES-{user.pk}', emp_code=f'RES-{user.pk}', first_name='Resource',
            last_name=user.username, email=user.email, join_date=date(2020, 1, 1), employment_status='active')

    def grant(self, user, *codes):
        role = Role.objects.create(code=f'identity-role-{user.pk}', name='Identity source reviewer')
        UserRole.objects.create(user_profile=user.rbac_profile, role=role)
        for code in codes:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            RoleModule.objects.get_or_create(role=role, module=module)
            for action in ('read', 'create', 'update'):
                Permission.objects.get_or_create(code=f'{code}.{action}', defaults={'name': action, 'action': action, 'module': module})
                for permission in module.permissions.filter(action=action, is_active=True):
                    RolePermission.objects.get_or_create(role=role, permission=permission)

    def link(self, adapter, row, targets, *, user=None, request_id=None, token=None):
        user = user or self.owner
        return link_record(adapter.key, row.pk, user, request_id=request_id or uuid4(),
            expected_token=token or version_token(adapter, row, user), targets=targets,
            reason='Reviewed original identity against the canonical record.')

    def connect_workspace(self):
        self.link(ADAPTERS['planning_project'], self.workspace, {'project_id': str(self.project.pk)})
        self.workspace.refresh_from_db()

    def hour_entry(self, **changes):
        wbs = WBSNode.objects.create(project=self.project, code='1', name='Engineering')
        account = ControlAccount.objects.create(project=self.project, wbs_node=wbs, code='ENG', name='Engineering',
            status='active', manager=self.owner, baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 1, 31))
        period = ReportingPeriod.objects.create(project=self.project, sequence=1, name='January',
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), data_date=date(2026, 1, 31))
        return ApprovedHourEntry.objects.create(project=self.project, control_account=account,
            reporting_period=period, employee_code='OLD-STAFF', employee_name='Original recorded person',
            work_date=date(2026, 1, 2), hours=8, hourly_cost_rate=25, labor_actual_cost=200,
            source_reference='SOURCE-1', **changes)

    def test_workspace_link_is_reviewed_repeat_safe_and_preserves_labels(self):
        adapter = ADAPTERS['planning_project']
        command = uuid4()
        token = version_token(adapter, self.workspace, self.owner)
        first = self.link(adapter, self.workspace, {'project_id': str(self.project.pk)}, request_id=command, token=token)
        second = self.link(adapter, self.workspace, {'project_id': str(self.project.pk)}, request_id=command, token=token)
        self.assertFalse(first['replayed'])
        self.assertTrue(second['replayed'])
        self.workspace.refresh_from_db()
        self.assertEqual((self.workspace.name, self.workspace.client), ('Legacy workspace', 'Legacy client'))
        self.assertEqual(self.workspace.enterprise_project_id, self.project.pk)
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 1)

    def test_workspace_duplicate_target_and_ordinary_patch_are_denied(self):
        self.connect_workspace()
        other = PlanningProject.objects.create(name='Another workspace', created_by=self.owner)
        with self.assertRaises(ValidationError):
            self.link(ADAPTERS['planning_project'], other, {'project_id': str(self.project.pk)})
        serializer = PlanningProjectSerializer(other, data={'enterprise_project': self.project.pk}, partial=True, context=self.context)
        self.assertFalse(serializer.is_valid())
        self.assertIn('enterprise_project', serializer.errors)

    def test_stale_source_and_unauthorized_target_do_not_link(self):
        adapter = ADAPTERS['planning_project']
        token = version_token(adapter, self.workspace, self.owner)
        self.workspace.name = 'Changed source'
        self.workspace.save()
        with self.assertRaises(LinkConflict):
            self.link(adapter, self.workspace, {'project_id': str(self.project.pk)}, token=token)
        foreign_org = Organization.objects.create(code='identity-foreign', name='Foreign')
        foreign_owner = self.user('foreign-owner', foreign_org)
        target = Project.objects.create(code='FOREIGN', name='Foreign project', owner=foreign_owner)
        with self.assertRaises(PermissionDenied):
            self.link(adapter, self.workspace, {'project_id': str(target.pk)})
        self.workspace.refresh_from_db()
        self.assertIsNone(self.workspace.enterprise_project_id)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    def test_roles_remain_unlinked_and_named_resource_requires_explicit_review(self):
        self.connect_workspace()
        resource = ScheduleResource.objects.create(project=self.workspace, code=self.employee.employee_code,
            name=self.employee.get_full_name(), resource_type='labor')
        adapter = ADAPTERS['schedule_resource']
        self.assertEqual(adapter.describe(resource, self.owner)['state'], 'not_applicable')
        self.assertIsNone(resource.employee_id)
        result = self.link(adapter, resource, {'employee_id': str(self.employee.pk)})
        self.assertEqual(result['record']['links']['employee']['id'], str(self.employee.pk))
        resource.refresh_from_db()
        serializer = ScheduleResourceSerializer(resource, data={'name': 'Another person'}, partial=True, context=self.context)
        self.assertFalse(serializer.is_valid())
        self.assertIn('name', serializer.errors)

    def test_resource_foreign_employee_equipment_and_frozen_schedule_are_denied(self):
        self.connect_workspace()
        resource = ScheduleResource.objects.create(project=self.workspace, code='CREW', name='Legacy crew')
        foreign_org = Organization.objects.create(code='employee-foreign', name='Foreign employee org')
        foreign = self.employee_for(self.user('foreign-worker', foreign_org))
        with self.assertRaises(PermissionDenied):
            self.link(ADAPTERS['schedule_resource'], resource, {'employee_id': str(foreign.pk)})
        equipment = ScheduleResource.objects.create(project=self.workspace, code='CRANE', name='Crane', resource_type='equipment')
        with self.assertRaises(ValidationError):
            self.link(ADAPTERS['schedule_resource'], equipment, {'employee_id': str(self.employee.pk)})
        schedule = Schedule.objects.create(project=self.workspace, code='MASTER', name='Master', planned_start=date(2026, 1, 1))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='approved')
        activity = ScheduleActivity.objects.create(version=version, external_id='A-1', name='Activity')
        ActivityAssignment.objects.create(activity=activity, resource=resource)
        with self.assertRaises(ValidationError):
            self.link(ADAPTERS['schedule_resource'], resource, {'employee_id': str(self.employee.pk)})
        resource.refresh_from_db()
        self.assertIsNone(resource.employee_id)

    def test_new_named_resource_rejects_identity_mismatch_and_serializes_uuid(self):
        self.connect_workspace()
        values = {'project': self.workspace.pk, 'code': self.employee.employee_code,
                  'name': self.employee.get_full_name(), 'employee': str(self.employee.pk)}
        invalid = ScheduleResourceSerializer(data={**values, 'name': 'Different person'}, context=self.context)
        self.assertFalse(invalid.is_valid())
        valid = ScheduleResourceSerializer(data=values, context=self.context)
        self.assertTrue(valid.is_valid(), valid.errors)
        row = valid.save()
        self.assertEqual(valid.data['employee'], str(self.employee.pk))
        self.assertEqual(row.employee_id, self.employee.pk)

    def test_approved_hour_identity_preserves_financial_and_source_evidence(self):
        entry = self.hour_entry(status='approved', approved_by=self.owner)
        self.link(CONTROL_ADAPTERS['approved_hours'], entry, {'employee_id': str(self.employee.pk)})
        entry.refresh_from_db()
        self.assertEqual(entry.employee_id, self.employee.pk)
        self.assertEqual((entry.employee_code, entry.employee_name), ('OLD-STAFF', 'Original recorded person'))
        self.assertEqual((entry.status, entry.approved_by_id, entry.hours, entry.labor_actual_cost),
                         ('approved', self.owner.pk, Decimal('8'), Decimal('200')))
        serializer = ApprovedHourEntrySerializer(entry, data={'employee_code': 'FORGED'}, partial=True, context=self.context)
        self.assertFalse(serializer.is_valid())

    def test_employee_identity_is_redacted_when_it_leaves_project_scope(self):
        self.connect_workspace()
        resource = ScheduleResource.objects.create(project=self.workspace, code='OLD-RESOURCE',
            name='Original resource label', employee=self.employee)
        entry = self.hour_entry(status='draft', employee=self.employee)
        foreign_org = Organization.objects.create(code='moved-employee-org', name='Moved employee')
        UserProfile.objects.filter(user=self.worker).update(organization=foreign_org)
        resource_data = ScheduleResourceSerializer(resource, context=self.context).data
        hour_data = ApprovedHourEntrySerializer(entry, context=self.context).data
        for data in (resource_data, hour_data):
            self.assertIsNone(data['employee'])
            self.assertIsNone(data['employee_identity'])
        self.assertEqual(resource_data['name'], 'Original resource label')
        self.assertEqual(hour_data['employee_name'], 'Original recorded person')
        # Internal snapshot serialization keeps stable IDs; HTTP projections enforce access.
        self.assertEqual(ScheduleResourceSerializer(resource).data['employee'], str(self.employee.pk))
        self.assertEqual(ApprovedHourEntrySerializer(entry).data['employee'], str(self.employee.pk))

    def test_legacy_hour_patch_cannot_add_or_clear_canonical_identity(self):
        entry = self.hour_entry(status='draft')
        serializer = ApprovedHourEntrySerializer(entry, data={'employee': str(self.employee.pk)}, partial=True, context=self.context)
        self.assertFalse(serializer.is_valid())
        self.link(CONTROL_ADAPTERS['approved_hours'], entry, {'employee_id': str(self.employee.pk)})
        entry.refresh_from_db()
        serializer = ApprovedHourEntrySerializer(entry, data={'employee': None}, partial=True, context=self.context)
        self.assertFalse(serializer.is_valid())

    def test_organizer_link_preserves_history_and_generic_link_attempt_rejects(self):
        row = OrganizerProject.objects.create(name='Tool workspace', code='SOURCE-CODE', client='Source client', created_by=self.owner)
        row.activity.create(tool_code='hmb_extractor', summary='Original extraction', created_by=self.owner)
        self.link(ORGANIZER_ADAPTERS['organizer_project'], row, {'project_id': str(self.project.pk)})
        row.refresh_from_db()
        self.assertEqual(row.enterprise_project_id, self.project.pk)
        self.assertEqual((row.name, row.code, row.client, row.activity.count()),
                         ('Tool workspace', 'SOURCE-CODE', 'Source client', 1))
        with self.assertRaises(ValidationError):
            _sanitize_payload({'enterprise_project': None}, ALLOWED_UPDATE_FIELDS)

    def test_http_queue_and_link_command_enforce_source_permissions_and_retry(self):
        root = '/api/v1/projects/shared-records/'
        response = self.client.get(root, {'source_type': 'planning_project'})
        self.assertEqual(response.status_code, 200, response.data)
        record = response.data['results'][0]
        url = root + f'planning_project/{self.workspace.pk}/link/'
        body = {'request_id': str(uuid4()), 'expected_token': record['expected_token'],
                'targets': {'project_id': str(self.project.pk)}, 'reason': 'Reviewed project source reference.'}
        for replayed in (False, True):
            result = self.client.post(url, body, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['replayed'], replayed)
        RoleModule.objects.filter(role__code=f'identity-role-{self.owner.pk}', module__code='planning_package').delete()
        hidden = self.client.get(root, {'source_type': 'planning_project'})
        self.assertEqual(hidden.status_code, 200, hidden.data)
        self.assertEqual(hidden.data['results'], [])
        self.assertEqual(self.client.post(url, body, format='json').status_code, 404)

    def test_http_new_hour_employee_selection_derives_labels_and_rejects_foreign_identity(self):
        entry = self.hour_entry(status='draft')
        body = {'project': self.project.pk, 'control_account': entry.control_account_id,
                'reporting_period': entry.reporting_period_id, 'work_date': '2026-01-03', 'hours': '4',
                'employee': str(self.employee.pk), 'source_reference': 'NEW-CANONICAL'}
        url = '/api/v1/project-control/approved-hours/'
        result = self.client.post(url, body, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['employee_code'], self.employee.employee_code)
        self.assertEqual(result.data['employee_name'], self.employee.get_full_name())
        foreign_org = Organization.objects.create(code='hour-foreign', name='Foreign hour employee org')
        foreign = self.employee_for(self.user('hour-foreign-worker', foreign_org))
        denied = self.client.post(url, {**body, 'employee': str(foreign.pk), 'source_reference': 'DENIED'}, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertFalse(ApprovedHourEntry.objects.filter(source_reference='DENIED').exists())

    def test_http_ordinary_resource_and_organizer_patches_cannot_bypass_review(self):
        self.connect_workspace()
        resource = ScheduleResource.objects.create(project=self.workspace, code='LEGACY', name='Legacy engineer')
        response = self.client.patch(f'/api/v1/planning-intelligence/resources/{resource.pk}/',
                                    {'employee': str(self.employee.pk)}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        workspace = OrganizerProject.objects.create(name='Tool workspace', created_by=self.owner)
        response = self.client.patch(f'/api/v1/project-organizer/projects/{workspace.pk}/',
                                    {'enterprise_project': self.project.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        workspace.refresh_from_db()
        resource.refresh_from_db()
        self.assertIsNone(workspace.enterprise_project_id)
        self.assertIsNone(resource.employee_id)

    def test_prevalidated_planning_update_cannot_detach_a_newly_reviewed_link(self):
        serializer = PlanningProjectSerializer(self.workspace, data={'enterprise_project': None,
            'name': 'Stale submitted name'}, partial=True, context=self.context)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.connect_workspace()
        view = PlanningProjectViewSet()
        view.request = SimpleNamespace(user=self.owner)
        with self.assertRaises(ValidationError):
            view.perform_update(serializer)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.enterprise_project_id, self.project.pk)
        self.assertEqual(self.workspace.name, 'Legacy workspace')
        self.assertEqual(SharedRecordLinkCommand.objects.filter(source_type='planning_project').count(), 1)

    def test_stale_organizer_metadata_instance_preserves_reviewed_link(self):
        workspace = OrganizerProject.objects.create(name='Tool workspace', created_by=self.owner)
        stale = OrganizerProject.objects.get(pk=workspace.pk)
        self.link(ORGANIZER_ADAPTERS['organizer_project'], workspace, {'project_id': str(self.project.pk)})
        # Reproduce an instance loaded before the reviewed link was committed.
        # The normal mutation path now also takes its row lock before reading.
        with patch('apps.project_organizer.views._get_accessible_project', return_value=(stale, None)):
            response = self.client.patch(f'/api/v1/project-organizer/projects/{workspace.pk}/',
                                        {'description': 'Updated tool description'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        workspace.refresh_from_db()
        self.assertEqual(workspace.enterprise_project_id, self.project.pk)
        self.assertEqual(workspace.description, 'Updated tool description')
        self.assertEqual(SharedRecordLinkCommand.objects.filter(source_type='organizer_project').count(), 1)
