"""The routed project DELETE archives its scope and retains protected history."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember, ProjectTask
from apps.project_control.models import CostLedgerEntry, ReportingPeriod
from apps.rbac.models import Module, Permission, Role, RoleModule, RolePermission, UserRole
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..access import accessible_projects, can_write_project, can_final_approve_defaults
from ..models import (DocumentIntelligenceRun, OperationalControlReport, OperationalEarningPolicy,
    PlanningAuditEvent, PlanningProfile, PlanningProject, Schedule, ScheduleBaseline, ScheduleBasis,
    ScheduleVersion)
from .test_scheduling_engine import grant_planning_test_actions


urlpatterns = [path('api/v1/projects/', include('apps.core.project_urls')),
               path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProjectArchivalTests(TestCase):
    def setUp(self):
        self.owner, self.manager, self.engineer, self.outsider, self.admin = [User.objects.create_user(
            username=f'archive-{index}', email=f'archive-{index}@example.test',
            is_staff=index == 4, is_superuser=index == 4) for index in range(5)]
        actors = (self.owner, self.manager, self.engineer, self.outsider, self.admin)
        grant_planning_test_actions(actors, ('read', 'create', 'update', 'delete', 'approve'))
        module, _ = Module.objects.get_or_create(code='project_control', defaults={'name': 'Project Control'})
        role, _ = Role.objects.get_or_create(code='project_archive_fixture', defaults={'name': 'Archive fixture'})
        RoleModule.objects.get_or_create(role=role, module=module)
        for action in ('read', 'delete'):
            Permission.objects.get_or_create(code=f'archive_fixture_{action}', defaults={
                'module': module, 'name': f'Archive fixture {action}', 'action': action})
        for permission in Permission.objects.filter(module=module, action__in=('read', 'delete'), is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        for actor in actors:
            UserRole.objects.get_or_create(user_profile=actor.rbac_profile, role=role)
        self.enterprise = Project.objects.create(code='ARCHIVE-1', name='Retained project', owner=self.owner)
        ProjectMember.objects.create(project=self.enterprise, user=self.manager, role='project_manager')
        ProjectMember.objects.create(project=self.enterprise, user=self.engineer, role='engineer')
        self.workspace = PlanningProject.objects.create(enterprise_project=self.enterprise, created_by=self.owner,
            name='Retained planning workspace')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/projects/{self.enterprise.pk}/'
        self.planning_url = f'/api/v1/planning-intelligence/projects/{self.workspace.pk}/'

    def history(self):
        now = timezone.now()
        run = DocumentIntelligenceRun.objects.create(project=self.workspace, status='succeeded', started_at=now)
        basis = ScheduleBasis.objects.create(project=self.workspace, source_run=run, status='approved',
            approved_by=self.manager, approved_at=now)
        profile = PlanningProfile.objects.create(project=self.workspace, code='HISTORY', name='Approved policy',
            version=1, status='approved', created_by=self.owner, approved_by=self.manager, approved_at=now,
            definition={'policy': 'Explicit retained policy'}, approved_snapshot={'version': 1}, content_fingerprint='a' * 64)
        schedule = Schedule.objects.create(project=self.workspace, code='MASTER', name='Retained schedule',
                                           planned_start=date(2026, 9, 1))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='baselined')
        baseline = ScheduleBaseline.objects.create(schedule=schedule, source_version=version, name='Approved baseline',
            snapshot={'activities': [], 'retained': True}, approved_by=self.manager, approved_at=now)
        policy = OperationalEarningPolicy.objects.create(project=self.workspace, baseline=baseline, name='Accepted earning basis',
            status='approved', created_by=self.owner, approved_by=self.manager, approved_at=now,
            definition={'activities': []}, baseline_fingerprint='b' * 64)
        period = ReportingPeriod.objects.create(project=self.enterprise, sequence=1, name='Retained period',
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), data_date=date(2026, 9, 21))
        report = OperationalControlReport.objects.create(project=self.workspace, baseline=baseline, policy=policy,
            reporting_period=period, created_by=self.owner, submitted_by=self.owner, published_by=self.manager,
            status='published', published_at=now, publication={'preview': {'metrics': {'progress_pct': '25.00'}}})
        event = PlanningAuditEvent.objects.create(project=self.workspace, actor=self.owner, action='history.existing',
            entity_type='ScheduleBasis', entity_id=str(basis.pk), after={'approved': True})
        task = ProjectTask.objects.create(project=self.enterprise, title='Retained assignment', assigned_to=self.engineer)
        ledger = CostLedgerEntry.objects.create(project=self.enterprise, entry_key='archive-preserved-actual',
            entry_type='actual', amount='120.00', currency='AED', entry_date=date(2026, 9, 20), status='posted')
        return [basis, profile, baseline, policy, report, event, task, ledger]

    def test_delete_archives_project_and_workspace_without_removing_protected_history(self):
        records = self.history()
        before = [deepcopy(type(row).objects.filter(pk=row.pk).values().get()) for row in records]
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204, response.data)
        self.enterprise.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertTrue(self.enterprise.is_deleted)
        self.assertTrue(self.workspace.is_deleted)
        self.assertEqual(self.enterprise.deleted_at, self.workspace.deleted_at)
        for row, expected in zip(records, before):
            self.assertEqual(type(row).objects.filter(pk=row.pk).values().get(), expected)
        event = PlanningAuditEvent.objects.get(project=self.workspace, action='project.archived')
        self.assertEqual(event.actor_id, self.owner.pk)
        self.assertEqual(event.metadata['enterprise_project_id'], self.enterprise.pk)
        self.assertTrue(event.after['planning_workspace_is_deleted'])
        for actor in (self.owner, self.manager, self.admin):
            self.client.force_authenticate(actor)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.get(self.planning_url).status_code, 404)
            listed = self.client.get('/api/v1/projects/').data
            rows = listed if isinstance(listed, list) else listed['results']
            self.assertNotIn(self.enterprise.pk, [row['id'] for row in rows])
            self.assertFalse(accessible_projects(actor).filter(pk=self.workspace.pk).exists())
            self.assertFalse(can_write_project(actor, self.workspace))
        self.assertEqual(self.client.delete(self.url).status_code, 404)
        self.assertEqual(PlanningAuditEvent.objects.filter(action='project.archived').count(), 1)

    def test_outsider_and_ordinary_member_cannot_archive(self):
        for actor, expected in ((self.outsider, 404), (self.engineer, 403)):
            self.client.force_authenticate(actor)
            response = self.client.delete(self.url)
            self.assertEqual(response.status_code, expected, response.data)
            self.assertIn('detail', response.data)
        self.enterprise.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertFalse(self.enterprise.is_deleted)
        self.assertFalse(self.workspace.is_deleted)
        self.assertFalse(PlanningAuditEvent.objects.filter(action='project.archived').exists())

    def test_assigned_manager_and_admin_can_archive(self):
        for index, actor in enumerate((self.manager, self.admin), start=2):
            enterprise = Project.objects.create(code=f'ARCHIVE-{index}', name='Managed project', owner=self.owner)
            if actor == self.manager:
                ProjectMember.objects.create(project=enterprise, user=actor, role='project_manager')
            workspace = PlanningProject.objects.create(enterprise_project=enterprise, created_by=self.owner)
            self.client.force_authenticate(actor)
            response = self.client.delete(f'/api/v1/projects/{enterprise.pk}/')
            self.assertEqual(response.status_code, 204, response.data)
            enterprise.refresh_from_db()
            workspace.refresh_from_db()
            self.assertTrue(enterprise.is_deleted and workspace.is_deleted)

    def test_archived_enterprise_is_excluded_even_if_legacy_workspace_remains_active(self):
        Project.objects.filter(pk=self.enterprise.pk).update(is_deleted=True, deleted_at=timezone.now())
        workspace = PlanningProject.objects.select_related('enterprise_project').get(pk=self.workspace.pk)
        self.assertFalse(workspace.is_deleted)
        for actor in (self.owner, self.manager, self.admin):
            self.client.force_authenticate(actor)
            self.assertFalse(accessible_projects(actor).filter(pk=workspace.pk).exists())
            self.assertFalse(can_write_project(actor, workspace))
            self.assertFalse(can_final_approve_defaults(actor, workspace))
            self.assertEqual(self.client.get(self.planning_url).status_code, 404)

    def test_audit_failure_rolls_back_both_archival_flags(self):
        with patch('apps.planning_intelligence.services.audit.record_event', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaisesMessage(RuntimeError, 'Audit unavailable'):
                self.client.delete(self.url)
        self.enterprise.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertFalse(self.enterprise.is_deleted)
        self.assertFalse(self.workspace.is_deleted)

    def test_project_without_planning_workspace_can_be_archived(self):
        enterprise = Project.objects.create(code='ARCHIVE-EMPTY', name='No planning workspace', owner=self.owner)
        response = self.client.delete(f'/api/v1/projects/{enterprise.pk}/')
        self.assertEqual(response.status_code, 204, response.data)
        enterprise.refresh_from_db()
        self.assertTrue(enterprise.is_deleted)
