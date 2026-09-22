"""A WBS draft is durable planning work, never a silently published baseline."""
from copy import deepcopy
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Role, RoleModule, RolePermission, UserProfile, UserRole, Permission
from apps.rbac.module_actions import ensure_module_actions

from ..models import (
    DocumentIntelligenceRun, IntelligenceFact, PlanningAuditEvent,
    ScheduleActivity, ScheduleBaseline, ScheduleVersion,
)
from ..services.preview_confirmation import confirmation_is_current, source_fingerprint
from .test_document_intelligence import DocumentIntelligenceFixture


class WorkBreakdownFixture(DocumentIntelligenceFixture):
    def setUp(self):
        super().setUp()
        organization = Organization.objects.create(name='WBS tests', code='wbs-test')
        self.role = Role.objects.create(name='WBS planner', code='wbs-planner', level=4)
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'update', 'create'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        for user in (self.owner, self.outsider):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            # Isolate this role from grants installed by migration seed data.
            UserRole.objects.filter(user_profile=profile).delete()
            UserRole.objects.create(user_profile=profile, role=self.role)
        self.file = self.source('scope.txt', 'sow', 'Single line diagram E-001. Cable list. HAZOP.')
        self.raw = {
            'detected_project_name': self.project.name,
            'detected_effective_date_text': '2026-10-05', 'detected_duration_months': 3,
            'disciplines': {'electrical': {'in_scope': True, 'deliverables': ['Single line diagram', 'Cable list']}},
            'hse_studies': ['HAZOP'], 'available_hse_studies': ['HAZOP'],
        }
        self.run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', source_file_ids=[self.file.pk],
            started_at=timezone.now(), finished_at=timezone.now(), fact_count=1,
            summary={'base_intelligence': deepcopy(self.raw), 'source_fingerprint': source_fingerprint(self.project)},
        )
        self.fact = IntelligenceFact.objects.create(
            run=self.run, source_file=self.file, fact_type='deliverable', key='deliverable:electrical:sld',
            value={'name': 'Single line diagram', 'discipline': 'electrical', 'document_number': 'E-001'},
            source_excerpt='Single line diagram E-001.', source_locator={'line': 1}, status='detected',
        )
        self.selection = {
            'detected_project_name': self.project.name,
            'detected_effective_date_text': '2026-10-05', 'detected_duration_months': 3,
            'disciplines': {'electrical': {
                'in_scope': True, 'deliverables': ['Single line diagram', 'Cable list', 'Design basis'],
                'excluded_deliverables': ['Cable list'],
            }}, 'hse_studies': ['HAZOP'],
        }
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.confirm_url = f'/api/v1/planning-intelligence/intelligence-runs/{self.run.pk}/confirm-preview/'
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/work-breakdown/'
        response = self.client.post(self.confirm_url, {'preview': self.selection}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def read(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return deepcopy(response.data)

    def save(self, draft, *, advance=False):
        return self.client.put(self.url, {**draft, 'advance': advance}, format='json')


class WorkBreakdownTests(WorkBreakdownFixture):
    def test_read_initializes_selected_deliverables_without_writes(self):
        draft = self.read()
        self.assertEqual({task['title'] for task in draft['tasks']}, {'Single line diagram', 'Design basis', 'HAZOP'})
        evidence = next(task for task in draft['tasks'] if task['title'] == 'Single line diagram')
        self.assertEqual(evidence['source_references'][0]['fact_id'], self.fact.pk)
        self.assertEqual(evidence['document_number'], 'E-001')
        self.assertTrue(all(task['effort_hours'] is None and task['owner'] == '' for task in draft['tasks']))
        self.assertEqual(draft['source_documents'][0]['name'], 'scope.txt')
        self.assertEqual(draft['tasks'], self.read()['tasks'])
        self.assertEqual(draft['revision'], 0)
        self.run.refresh_from_db()
        self.assertNotIn('work_breakdown_drafts', self.run.summary)
        self.assertFalse(self.project.schedules.exists())

    def test_save_restores_edits_without_changing_confirmation_or_raw_evidence(self):
        draft = self.read()
        task = draft['tasks'][0]
        task.update(owner='A. Khan', effort_hours=24, acceptance_criteria='Checked against scope', reviewer='S. Joseph')
        task['source_references'] = [{'file_id': 999, 'excerpt': 'Forged'}]
        draft['tasks'][1]['depends_on'] = [task['id']]
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        restored = self.read()
        self.assertEqual(restored['revision'], 1)
        self.assertEqual(restored['tasks'][0]['owner'], 'A. Khan')
        self.assertEqual(restored['tasks'][0]['effort_hours'], 24)
        self.assertEqual(restored['tasks'][1]['depends_on'], [task['id']])
        self.assertEqual(restored['tasks'][0]['source_references'][0]['fact_id'], self.fact.pk)
        self.run.refresh_from_db()
        self.assertTrue(confirmation_is_current(self.run))
        self.assertEqual(self.run.summary['base_intelligence'], self.raw)
        self.assertEqual(PlanningAuditEvent.objects.filter(action='work_breakdown.saved').count(), 1)
        self.assertFalse(self.project.schedules.exists())

    def test_reconfirmation_starts_a_new_draft_and_retains_prior_draft(self):
        draft = self.read()
        draft['tasks'][0]['owner'] = 'Original owner'
        self.assertEqual(self.save(draft).status_code, 200)
        self.selection['hse_studies'] = []
        self.assertEqual(self.client.post(self.confirm_url, {'preview': self.selection}, format='json').status_code, 200)
        refreshed = self.read()
        self.assertEqual(refreshed['revision'], 0)
        self.assertNotIn('HAZOP', [task['title'] for task in refreshed['tasks']])
        self.assertEqual(refreshed['tasks'][0]['owner'], '')
        self.assertEqual(self.save(draft).status_code, 409)
        self.run.refresh_from_db()
        self.assertEqual(self.run.summary['work_breakdown_drafts'][draft['preview_confirmed_at']]['tasks'][0]['owner'], 'Original owner')

    def test_changed_sources_block_read_save_and_advance(self):
        draft = self.read()
        self.file.parse_status = 'pending'
        self.file.save()
        self.assertEqual(self.client.get(self.url).status_code, 409)
        self.assertEqual(self.save(draft, advance=True).status_code, 409)
        self.assertFalse(self.project.schedules.exists())

    def test_concurrent_save_is_rejected(self):
        first = self.read()
        second = deepcopy(first)
        first['tasks'][0]['owner'] = 'First planner'
        self.assertEqual(self.save(first).status_code, 200)
        second['tasks'][0]['owner'] = 'Second planner'
        response = self.save(second)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'work_breakdown_revision_conflict')
        self.assertEqual(self.read()['tasks'][0]['owner'], 'First planner')

    def test_dependencies_require_unique_ids_existing_tasks_and_no_cycles(self):
        original = self.read()
        for invalid in ('duplicate', 'missing', 'self', 'cycle'):
            draft = deepcopy(original)
            first, second = draft['tasks'][:2]
            if invalid == 'duplicate':
                second['id'] = first['id']
            elif invalid == 'missing':
                first['depends_on'] = ['missing-task']
            elif invalid == 'self':
                first['depends_on'] = [first['id']]
            else:
                first['depends_on'] = [second['id']]
                second['depends_on'] = [first['id']]
            with self.subTest(invalid=invalid):
                self.assertEqual(self.save(draft).status_code, 400)
        self.assertEqual(self.read()['revision'], 0)

    def test_effort_rejects_negative_and_nonfinite_values(self):
        for hours in (-1, 'NaN', 'Infinity', '-Infinity'):
            draft = self.read()
            draft['tasks'][0]['effort_hours'] = hours
            with self.subTest(hours=hours):
                self.assertEqual(self.save(draft).status_code, 400)
        draft = self.read()
        draft['tasks'][0]['effort_hours'] = 0
        self.assertEqual(self.save(draft).status_code, 200)

    def test_other_project_and_read_only_permission_cannot_save(self):
        draft = self.read()
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.save(draft).status_code, 404)
        self.client.force_authenticate(self.owner)
        RolePermission.objects.filter(role=self.role, permission__action='update').delete()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.save(draft).status_code, 403)

    def test_advance_materializes_effort_owners_and_dependencies_without_duration_or_baseline(self):
        draft = self.read()
        draft['tasks'][0].update(owner='A. Khan', effort_hours=24, acceptance_criteria='Drawing checked', reviewer='S. Joseph')
        draft['tasks'][1]['depends_on'] = [draft['tasks'][0]['id']]
        response = self.save(draft, advance=True)
        self.assertEqual(response.status_code, 200, response.data)
        version = ScheduleVersion.objects.get(pk=response.data['schedule_version_id'])
        self.assertEqual(version.status, 'draft')
        self.assertEqual(version.activities.count(), len(draft['tasks']))
        first = version.activities.get(external_id=draft['tasks'][0]['id'])
        self.assertEqual(first.metadata['evidence_entity_id'], 'task:' + draft['tasks'][0]['id'])
        self.assertEqual(first.responsible_role, 'A. Khan')
        self.assertEqual(first.duration_days, 0)
        self.assertTrue(first.metadata['duration_pending'])
        self.assertEqual(first.metadata['planned_effort_hours'], 24)
        self.assertEqual(first.metadata['reviewer'], 'S. Joseph')
        self.assertEqual(first.assignments.get().planned_units, 24)
        relationship = version.relationships.get()
        self.assertEqual(relationship.predecessor_id, first.pk)
        self.assertEqual(relationship.successor.external_id, draft['tasks'][1]['id'])
        self.assertFalse(version.calculation_runs.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertFalse(self.project.generations.exists())
        self.assertFalse(self.project.schedule_bases.exists())
        again = self.save(response.data, advance=True)
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(again.data['schedule_version_id'], version.pk)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_revised_wbs_creates_new_version_and_preserves_approved_schedule(self):
        response = self.save(self.read(), advance=True)
        self.assertEqual(response.status_code, 200, response.data)
        old = ScheduleVersion.objects.get(pk=response.data['schedule_version_id'])
        old.status = 'baselined'
        old.save(update_fields=['status'])
        baseline = ScheduleBaseline.objects.create(schedule=old.schedule, source_version=old, name='Approved', snapshot={'kept': True})
        draft = deepcopy(response.data)
        draft['tasks'][0]['title'] = 'Revised drawing'
        updated = self.save(draft, advance=True)
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertNotEqual(updated.data['schedule_version_id'], old.pk)
        old.refresh_from_db()
        baseline.refresh_from_db()
        self.assertEqual(old.status, 'baselined')
        self.assertEqual(baseline.snapshot, {'kept': True})
        self.assertNotEqual(old.activities.first().name, 'Revised drawing')

    def test_empty_draft_can_save_but_not_advance(self):
        draft = self.read()
        draft['tasks'] = []
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.save(response.data, advance=True).status_code, 409)
        self.assertFalse(ScheduleActivity.objects.exists())

    def test_unchanged_approved_version_is_preserved_and_reopened_as_new_draft(self):
        response = self.save(self.read(), advance=True)
        self.assertEqual(response.status_code, 200, response.data)
        approved = ScheduleVersion.objects.get(pk=response.data['schedule_version_id'])
        approved.status = 'approved'
        approved.save(update_fields=['status'])
        repeated = self.save(response.data, advance=True)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertNotEqual(repeated.data['schedule_version_id'], approved.pk)
        next_version = ScheduleVersion.objects.get(pk=repeated.data['schedule_version_id'])
        self.assertEqual(next_version.status, 'draft')
        self.assertEqual(next_version.parent_version_id, approved.pk)
        approved.refresh_from_db()
        self.assertEqual(approved.status, 'approved')

    def test_audit_failure_rolls_back_draft_and_schedule(self):
        draft = self.read()
        with patch('apps.planning_intelligence.services.work_breakdown.record_event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.save(draft, advance=True)
        self.assertEqual(self.read()['revision'], 0)
        self.assertFalse(self.project.schedules.exists())
