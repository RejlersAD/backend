"""The routed project DELETE is explicit and permanent, with protected-history guards."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember, ProjectTask
from apps.project_control.models import CostLedgerEntry, ReportingPeriod
from apps.rbac.models import AuditLog, Module, Permission, Role, RoleModule, RolePermission, UserRole
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..access import accessible_projects, can_write_project, can_final_approve_defaults
from ..models import (DocumentIntelligenceRun, OperationalControlReport, OperationalEarningPolicy,
    PlanningAuditEvent, PlanningJob, PlanningProfile, PlanningProject, PlanningRetentionPolicy,
    Schedule, ScheduleBaseline, ScheduleBasis,
    ScheduleVersion)
from .test_scheduling_engine import grant_planning_test_actions


urlpatterns = [path('api/v1/projects/', include('apps.core.project_urls')),
               path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProjectPermanentDeletionTests(TestCase):
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

    def delete(self, project=None):
        project = project or self.enterprise
        return self.client.delete(f'/api/v1/projects/{project.pk}/', {
            'permanent': True, 'expected_updated_at': project.updated_at.isoformat(),
        }, format='json')

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

    def test_protected_history_blocks_deletion_without_archiving(self):
        records = self.history()
        before = [deepcopy(type(row).objects.filter(pk=row.pk).values().get()) for row in records]
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_protected')
        self.assertTrue(response.data['blockers'])
        self.enterprise.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertFalse(self.enterprise.is_deleted)
        self.assertFalse(self.workspace.is_deleted)
        for row, expected in zip(records, before):
            self.assertEqual(type(row).objects.filter(pk=row.pk).values().get(), expected)
        self.assertFalse(AuditLog.objects.filter(metadata__command='permanently_delete_project').exists())

    def test_delete_removes_project_workspace_tasks_and_findings_permanently(self):
        from ..models import IntelligenceFact
        task = ProjectTask.objects.create(project=self.enterprise, title='Project task', assigned_to=self.engineer)
        archived_task = ProjectTask.objects.create(project=self.enterprise, title='Previously removed task', is_deleted=True)
        run = DocumentIntelligenceRun.objects.create(project=self.workspace, status='succeeded', started_at=timezone.now())
        finding = IntelligenceFact.objects.create(run=run, fact_type='requirement', key='rejected-draft',
            value='Draft finding', status='rejected', reviewed_by=self.owner, reviewed_at=timezone.now())
        PlanningAuditEvent.objects.create(project=self.workspace, actor=self.owner, action='history.existing', entity_type='Project')
        response = self.delete()
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Project.objects.filter(pk=self.enterprise.pk).exists())
        self.assertFalse(PlanningProject.objects.filter(pk=self.workspace.pk).exists())
        self.assertFalse(ProjectTask.objects.filter(pk__in=[task.pk, archived_task.pk]).exists())
        self.assertFalse(DocumentIntelligenceRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(IntelligenceFact.objects.filter(pk=finding.pk).exists())
        event = AuditLog.objects.get(metadata__command='permanently_delete_project')
        self.assertEqual(event.user_id, self.owner.pk)
        self.assertEqual(event.metadata['project_id'], self.enterprise.pk)
        self.assertTrue(event.changes['after']['permanently_deleted'])
        self.assertTrue(User.objects.filter(pk=self.engineer.pk).exists())
        for actor in (self.owner, self.manager, self.admin):
            self.client.force_authenticate(actor)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.get(self.planning_url).status_code, 404)
            listed = self.client.get('/api/v1/projects/').data
            rows = listed if isinstance(listed, list) else listed['results']
            self.assertNotIn(self.enterprise.pk, [row['id'] for row in rows])
            self.assertFalse(accessible_projects(actor).filter(pk=self.workspace.pk).exists())
        self.assertEqual(self.delete().status_code, 404)
        self.assertEqual(AuditLog.objects.filter(metadata__command='permanently_delete_project').count(), 1)

    def test_outsider_and_ordinary_member_cannot_delete(self):
        for actor, expected in ((self.outsider, 404), (self.engineer, 403)):
            self.client.force_authenticate(actor)
            response = self.delete()
            self.assertEqual(response.status_code, expected, response.data)
            self.assertIn('detail', response.data)
        self.enterprise.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertFalse(self.enterprise.is_deleted)
        self.assertFalse(self.workspace.is_deleted)
        self.assertFalse(PlanningAuditEvent.objects.filter(action='project.archived').exists())

    def test_assigned_manager_and_admin_can_permanently_delete(self):
        for index, actor in enumerate((self.manager, self.admin), start=2):
            enterprise = Project.objects.create(code=f'ARCHIVE-{index}', name='Managed project', owner=self.owner)
            if actor == self.manager:
                ProjectMember.objects.create(project=enterprise, user=actor, role='project_manager')
            workspace = PlanningProject.objects.create(enterprise_project=enterprise, created_by=self.owner)
            self.client.force_authenticate(actor)
            response = self.delete(enterprise)
            self.assertEqual(response.status_code, 204, response.data)
            self.assertFalse(Project.objects.filter(pk=enterprise.pk).exists())
            self.assertFalse(PlanningProject.objects.filter(pk=workspace.pk).exists())

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

    def test_audit_failure_rolls_back_project_and_workspace_deletion(self):
        with patch('apps.core.project_deletion.AuditLog.objects.create', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaisesMessage(RuntimeError, 'Audit unavailable'):
                self.delete()
        self.enterprise.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertFalse(self.enterprise.is_deleted)
        self.assertFalse(self.workspace.is_deleted)

    def test_failure_after_database_delete_restores_rows_and_removes_audit(self):
        from django.db.models.deletion import Collector
        original_delete = Collector.delete

        def fail_after_delete(collector):
            original_delete(collector)
            raise RuntimeError('Deletion interrupted')

        with patch('apps.core.project_deletion.Collector.delete', autospec=True, side_effect=fail_after_delete):
            with self.assertRaisesMessage(RuntimeError, 'Deletion interrupted'):
                self.delete()
        self.assertTrue(Project.objects.filter(pk=self.enterprise.pk).exists())
        self.assertTrue(PlanningProject.objects.filter(pk=self.workspace.pk).exists())
        self.assertFalse(AuditLog.objects.filter(metadata__command='permanently_delete_project').exists())

    def test_project_without_planning_workspace_can_be_permanently_deleted(self):
        enterprise = Project.objects.create(code='ARCHIVE-EMPTY', name='No planning workspace', owner=self.owner)
        response = self.delete(enterprise)
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(Project.objects.filter(pk=enterprise.pk).exists())

    def test_old_archive_client_and_unconfirmed_requests_cannot_delete(self):
        for data in ({}, {'permanent': False, 'expected_updated_at': self.enterprise.updated_at.isoformat()},
                     {'permanent': True}):
            response = self.client.delete(self.url, data, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.enterprise.refresh_from_db()
        self.assertFalse(self.enterprise.is_deleted)
        self.assertTrue(PlanningProject.objects.filter(pk=self.workspace.pk).exists())

    def test_stale_project_cannot_be_deleted(self):
        Project.objects.filter(pk=self.enterprise.pk).update(updated_at=timezone.now())
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_stale')
        self.assertTrue(Project.objects.filter(pk=self.enterprise.pk, is_deleted=False).exists())

    def test_active_jobs_and_legal_hold_block_without_archiving(self):
        job = PlanningJob.objects.create(project=self.workspace, job_type='analyze', status='running')
        self.assertEqual(self.delete().data['code'], 'project_delete_work_active')
        job.status = 'succeeded'
        job.save()
        PlanningRetentionPolicy.objects.create(project=self.workspace, legal_hold=True)
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_legal_hold')
        self.assertTrue(Project.objects.filter(pk=self.enterprise.pk, is_deleted=False).exists())

    def test_direct_evidence_assertion_blocks_even_without_a_protected_relation(self):
        import uuid
        from ..models import EvidenceGraph, EvidenceNode
        graph = EvidenceGraph.objects.create(project=self.workspace, schema_version='1', rule_version='1')
        EvidenceNode.objects.create(id=uuid.uuid4(), graph=graph, kind='fact', entity_id='a',
                                    entity_name='Source assertion', provenance_type='document_evidence')
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_protected')
        self.assertTrue(Project.objects.filter(pk=self.enterprise.pk, is_deleted=False).exists())

    def test_posted_cost_ledger_is_not_removed_by_fast_cascade(self):
        ledger = CostLedgerEntry.objects.create(project=self.enterprise, entry_key='delete-protected-actual',
            entry_type='actual', amount='120.00', currency='AED', entry_date=date(2026, 9, 20), status='posted')
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_protected')
        self.assertTrue(CostLedgerEntry.objects.filter(pk=ledger.pk).exists())

    def test_linked_workspace_creation_rechecks_project_after_validation(self):
        from types import SimpleNamespace
        from rest_framework.exceptions import ValidationError
        from ..serializers import PlanningProjectSerializer
        from ..views import PlanningProjectViewSet
        enterprise = Project.objects.create(code='REMOVED-BEFORE-CREATE', name='Concurrent deletion', owner=self.owner)
        request = SimpleNamespace(user=self.owner)
        serializer = PlanningProjectSerializer(data={'enterprise_project': enterprise.pk, 'name': 'No orphan'},
                                              context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        enterprise_id = enterprise.pk
        enterprise.delete()
        view = PlanningProjectViewSet()
        view.request = request
        with self.assertRaises(ValidationError):
            view.perform_create(serializer)
        self.assertFalse(PlanningProject.objects.filter(enterprise_project_id=enterprise_id).exists())

    def test_linked_workspace_creation_still_succeeds_for_available_project(self):
        enterprise = Project.objects.create(code='CREATE-VALID', name='Available project', owner=self.owner)
        response = self.client.post('/api/v1/planning-intelligence/projects/',
                                    {'enterprise_project': enterprise.pk, 'name': 'New linked workspace'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(PlanningProject.objects.filter(enterprise_project=enterprise, created_by=self.owner).exists())

    def test_busy_enterprise_lock_returns_conflict_without_deleting(self):
        from django.db import OperationalError
        cause = RuntimeError('lock unavailable')
        cause.sqlstate = '55P03'
        error = OperationalError('lock unavailable')
        error.__cause__ = cause
        with patch('apps.core.project_deletion.Project.objects.select_for_update', side_effect=error):
            response = self.delete()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_busy')
        self.assertTrue(Project.objects.filter(pk=self.enterprise.pk, is_deleted=False).exists())
        self.assertTrue(PlanningProject.objects.filter(pk=self.workspace.pk).exists())

    def test_linked_workspace_cannot_be_archived_through_legacy_delete(self):
        response = self.client.delete(self.planning_url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'project_delete_from_project_control')
        self.workspace.refresh_from_db()
        self.assertFalse(self.workspace.is_deleted)

    def test_project_delete_module_permission_is_required(self):
        RolePermission.objects.filter(permission__module__code='project_control', permission__action='delete').delete()
        response = self.delete()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Project.objects.filter(pk=self.enterprise.pk).exists())

    def test_shared_procurement_project_registry_survives(self):
        from apps.procurement.models_master import Project as ProcurementProject
        wrapper = ProcurementProject.objects.create(enterprise_project=self.enterprise,
            project_number='SHARED-REGISTRY', project_name='Existing procurement registry')
        response = self.delete()
        self.assertEqual(response.status_code, 204, response.data)
        wrapper.refresh_from_db()
        self.assertIsNone(wrapper.enterprise_project_id)
        self.assertEqual(wrapper.project_name, 'Existing procurement registry')
