"""Department guidance stays stage-specific, calendar-based and revision-safe."""
import datetime as dt
from unittest.mock import patch

from rest_framework.test import APIClient
from apps.core.project_models import ProjectMember
from apps.users.models import User

from ..models import CalendarException, PlanningAuditEvent, PlanningGeneration
from ..services.cpm import WorkdayCalendar, calculate_schedule_version
from ..services.operational_jobs import schedule_state_fingerprint
from ..services.piping_guidance import (
    ALIASES, DURATIONS, EVENTS, RULES, VERSION, activity_identity, apply_piping_guidance,
    enabled, guidance_findings, preview_piping_guidance,
)
from ..services.schedule_approval import ScheduleApprovalError
from ..services.trustworthy_scheduling import run_schedule_assurance
from .test_scheduling_engine import ScheduleAPIFixture, grant_planning_test_actions


class PipingGuidanceTests(ScheduleAPIFixture):
    def setUp(self):
        super().setUp()
        self.project.planned_end_date = dt.date(2027, 8, 24)
        self.project.save()
        endpoints = {gate for left, right, _kind, _lag in RULES for gate in (left, right)}
        # Equivalent IFA/IFC rows already present must be used without waiting
        # for final issue. They are not silently created by applying guidance.
        endpoints.update((family, stage) for family in ('process_basis', 'piping_basis', 'pids', 'pms', 'valve_spec', 'valve_datasheet', 'valve_mr', 'plot_plan', 'model_procedure') for stage in ('IFR', 'IFA', 'IFC'))
        self.gates = {}
        for index, (family, stage) in enumerate(sorted(endpoints)):
            title = ALIASES[family][0]
            event = family in EVENTS
            self.gates[(family, stage)] = self.activity(str(100 + index * 10), 0 if event else 1,
                activity_type='finish_milestone' if event else 'task',
                discipline='process' if family in {'process_basis', 'pids'} else 'piping',
                metadata={'deliverable': title, 'workflow_stage_code': stage})
            row = self.gates[(family, stage)]
            row.name = title if event else f'{title} ({stage})'
            row.save()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/piping-guidance/'

    def preview(self):
        self.version.refresh_from_db()
        return preview_piping_guidance(self.version)

    def apply(self):
        plan = self.preview()
        self.assertEqual(plan['issues'], [])
        result = apply_piping_guidance(self.version, self.owner, preview_token=plan['preview_token'])
        self.version.refresh_from_db()
        return result

    def test_preview_is_read_only_and_has_only_the_two_explicit_lags(self):
        plan = self.preview()
        self.assertTrue(plan['can_apply'], plan['issues'])
        self.assertEqual({(row['type'], row['lag_days']) for row in plan['add_links']}, {('FS', 0), ('FF', 3), ('FF', 4)})
        self.assertEqual(len(plan['duration_changes']), 7)
        self.assertFalse(self.version.relationships.exists())
        self.assertFalse(enabled(self.version))
        self.assertFalse(PlanningAuditEvent.objects.filter(action='schedule.piping_guidance_applied').exists())

    def test_corrected_vendor_sequence_and_equivalent_revisions(self):
        mr = self.gates['valve_mr', 'IFA']
        offers, receipt, tbe = (self.gates[family, stage] for family, stage in (
            ('vendor_offers', 'EVENT'), ('offer_receipt', 'EVENT'), ('valve_tbe', 'IFC')))
        self.link(mr, receipt, 'FS', 8)
        self.link(offers, tbe, 'FS', 9)
        self.apply()
        for left, right in ((mr, offers), (offers, receipt), (receipt, tbe)):
            self.assertTrue(self.version.relationships.filter(predecessor=left, successor=right, relationship_type='FS', lag_days=0, is_deleted=False).exists())
        self.assertFalse(self.version.relationships.filter(predecessor=mr, successor=receipt, is_deleted=False).exists())
        for stage in ('IFR', 'IFA', 'IFC'):
            self.assertTrue(self.version.relationships.filter(predecessor=self.gates['pms', stage], successor=self.gates['valve_spec', stage], relationship_type='FS', lag_days=0, is_deleted=False).exists())
        for stage, kind, lag in (('IFR', 'FF', 3), ('IFA', 'FS', 0), ('IFC', 'FS', 0)):
            self.assertTrue(self.version.relationships.filter(predecessor=self.gates['pids', stage], successor=self.gates['plot_plan', stage], relationship_type=kind, lag_days=lag, is_deleted=False).exists())

    def test_positive_duration_milestones_become_tasks_without_source_rewrite(self):
        self.apply()
        for family, duration in DURATIONS.items():
            activity = self.gates[family, 'EVENT']
            original_name = activity.name
            activity.refresh_from_db()
            self.assertEqual(activity.duration_days, duration)
            self.assertFalse(activity.is_milestone)
            self.assertEqual(activity.name, original_name)
            self.assertEqual(activity.metadata['pre_guidance_activity_type'], 'finish_milestone')
        self.project.refresh_from_db()
        self.assertEqual(self.project.planned_end_date, dt.date(2027, 8, 24))
        self.assertTrue(PlanningAuditEvent.objects.filter(action='schedule.piping_guidance_applied').exists())
        calculate_schedule_version(self.version)
        offers = self.gates['vendor_offers', 'EVENT']
        offers.refresh_from_db()
        calendar = WorkdayCalendar(self.calendar, self.schedule.planned_start)
        self.assertEqual(calendar.index_of(offers.planned_finish) - calendar.index_of(offers.planned_start) + 1, 15)

    def test_repeated_application_has_no_duplicate_links_or_duration_changes(self):
        self.apply()
        count = self.version.relationships.filter(is_deleted=False).count()
        self.apply()
        plan = self.preview()
        self.assertEqual(plan['add_links'], [])
        self.assertEqual(plan['remove_links'], [])
        self.assertEqual(plan['duration_changes'], [])
        self.assertEqual(self.version.relationships.filter(is_deleted=False).count(), count)

    def test_missing_ifc_is_not_replaced_with_generic_final_issue(self):
        tbe = self.gates['valve_tbe', 'IFC']
        tbe.metadata['workflow_stage_code'] = 'FINAL_ISSUE'
        tbe.name = 'TBE for Valves - Final issue'
        tbe.save()
        plan = self.preview()
        self.assertFalse(plan['can_apply'])
        self.assertIn('piping_stage_missing', {row['code'] for row in plan['issues']})
        self.assertEqual(activity_identity(tbe), ('valve_tbe', 'FINAL_ISSUE'))

    def test_missing_required_endpoint_and_conflicting_stage_name_block_application(self):
        receipt = self.gates['offer_receipt', 'EVENT']
        receipt.soft_delete()
        self.assertIn('piping_endpoint_missing', {row['code'] for row in self.preview()['issues']})
        receipt.is_deleted, receipt.deleted_at = False, None
        receipt.save()
        stage = self.gates['pms', 'IFR']
        stage.name = 'PMS (IFA)'
        stage.save()
        self.assertIn('piping_stage_conflict', {row['code'] for row in self.preview()['issues']})

    def test_idempotent_reapply_keeps_a_current_calculation(self):
        self.apply()
        calculate_schedule_version(self.version)
        self.version.refresh_from_db()
        timestamp = self.version.updated_at
        result = self.apply()
        self.assertFalse(result['changed'])
        self.assertEqual(self.version.updated_at, timestamp)
        self.assertEqual(self.version.status, 'calculated')

    def test_ambiguous_gate_and_manual_cycle_are_rejected_without_writes(self):
        duplicate = self.activity('DUPLICATE', 1, metadata=self.gates['pms', 'IFR'].metadata)
        self.assertIn('piping_gate_ambiguous', {row['code'] for row in self.preview()['issues']})
        duplicate.soft_delete()
        self.link(self.gates['pms', 'IFR'], self.gates['process_basis', 'IFR'])
        plan = self.preview()
        self.assertIn('piping_guidance_cycle', {row['code'] for row in plan['issues']})
        with self.assertRaises(ScheduleApprovalError):
            apply_piping_guidance(self.version, self.owner, preview_token=plan['preview_token'])
        self.assertFalse(enabled(self.version))

    def test_stale_preview_and_cross_project_requests_cannot_apply(self):
        plan = self.preview()
        activity = self.gates['pms', 'IFR']
        activity.duration_days = 3
        activity.save()
        response = self.client.post(self.url, {'preview_token': plan['preview_token']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'version_conflict')
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'preview_token': plan['preview_token']}).status_code, 404)
        self.assertFalse(self.version.relationships.exists())

    def test_read_only_member_can_preview_but_cannot_write(self):
        reader = User.objects.create_user(username='piping-reader', email='piping-reader@example.com', password='test')
        grant_planning_test_actions((reader,), ('read',))
        ProjectMember.objects.create(project=self.project.enterprise_project, user=reader, role='viewer')
        self.client.force_authenticate(reader)
        preview = self.client.get(self.url)
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data['can_apply'])
        self.assertEqual(self.client.post(self.url, {'preview_token': preview.data['preview_token']}).status_code, 403)
        self.assertFalse(self.version.relationships.exists())

    def test_approved_and_old_revisions_remain_immutable(self):
        self.version.status = 'approved'
        self.version.save()
        self.assertFalse(self.preview()['can_apply'])
        response = self.client.post(self.url, {'preview_token': self.preview()['preview_token']})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.version.relationships.exists())

    def test_deadline_uses_working_calendar_holidays_and_contract_not_extended_target(self):
        CalendarException.objects.create(calendar=self.calendar, date=dt.date(2026, 8, 25), is_working=False, name='Holiday')
        calendar = WorkdayCalendar(self.calendar, self.schedule.planned_start)
        self.project.planned_end_date = calendar.date_at(19)
        self.project.save()
        self.version.evidence_input_snapshot = {'planner_adjustments': {'planning_target_finish': '2030-12-31'}}
        self.version.save()
        deadline = self.preview()['deadline']
        self.assertEqual(deadline['project_working_days'], 20)
        self.assertEqual(deadline['piping_working_days'], 18)
        self.assertEqual(deadline['finish_by'], calendar.date_at(17).isoformat())
        self.assertEqual(deadline['contingency_working_days'], 2)

    def test_cpm_and_assurance_report_infeasible_ninety_percent_deadline(self):
        self.project.planned_end_date = dt.date(2026, 9, 18)
        self.project.save()
        self.apply()
        calculate_schedule_version(self.version)
        self.version.refresh_from_db()
        review = run_schedule_assurance(self.version)
        self.assertIn('piping_ninety_percent_overrun', {row['code'] for row in review.blockers})
        self.assertEqual(self.version.activities.get(pk=self.gates['vendor_offers', 'EVENT'].pk).duration_days, 15)

    def test_discipline_or_link_changes_invalidate_guidance_assurance(self):
        self.apply()
        before = schedule_state_fingerprint(self.version)
        row = self.gates['pms', 'IFR']
        row.discipline = 'mechanical'
        row.save()
        self.assertNotEqual(schedule_state_fingerprint(self.version), before)
        link = self.version.relationships.filter(is_deleted=False).first()
        link.lag_days = 7
        link.save()
        self.assertIn('piping_guidance_changed', {row['code'] for row in guidance_findings(self.version)})

    def test_generated_reset_reapplies_the_policy_and_preserves_fixed_durations(self):
        self.apply()
        left, right = self.gates['pms', 'IFR'], self.gates['valve_spec', 'IFR']
        generation = PlanningGeneration.objects.create(project=self.project, version=1, generated_by=self.owner,
            intelligence={'schedule_engine': {'policy': 'planning_package'}},
            logic_matrix=[{'predecessor_id': left.external_id, 'successor_id': right.external_id, 'type': 'SS', 'lag_days': 9}])
        self.version.source_generation = generation
        self.version.save()
        response = self.client.post(self.url.replace('piping-guidance/', 'rebuild-generated-logic/'))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(self.version.relationships.filter(predecessor=left, successor=right, relationship_type='FS', lag_days=0, is_deleted=False).exists())
        self.assertEqual(self.preview()['add_links'], [])

    def test_new_revision_retains_guidance_with_new_activity_ids(self):
        self.apply()
        old_ids = set(self.version.activities.values_list('pk', flat=True))
        response = self.client.post(f'/api/v1/planning-intelligence/schedules/{self.schedule.pk}/create-version/')
        self.assertEqual(response.status_code, 201, response.data)
        revision = type(self.version).objects.get(pk=response.data['id'])
        self.assertEqual(enabled(revision)['version'], VERSION)
        self.assertFalse(old_ids.intersection(revision.activities.values_list('pk', flat=True)))
        plan = preview_piping_guidance(revision)
        self.assertEqual(plan['issues'], [])
        self.assertEqual(plan['add_links'], [])
        self.assertEqual(plan['duration_changes'], [])

    def test_reset_conflict_rolls_back_rebuilt_relationships(self):
        self.apply()
        before = list(self.version.relationships.filter(is_deleted=False).order_by('pk').values('pk', 'predecessor_id', 'successor_id', 'relationship_type', 'lag_days'))
        predecessor, successor = self.gates['pms', 'IFR'], self.gates['process_basis', 'IFR']
        generation = PlanningGeneration.objects.create(project=self.project, version=1, generated_by=self.owner,
            intelligence={'schedule_engine': {'policy': 'planning_package'}},
            logic_matrix=[{'predecessor_id': predecessor.external_id, 'successor_id': successor.external_id, 'type': 'FS', 'lag_days': 0}])
        self.version.source_generation = generation
        self.version.save()
        response = self.client.post(self.url.replace('piping-guidance/', 'rebuild-generated-logic/'))
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'piping_guidance_reset_conflict')
        self.assertEqual(before, list(self.version.relationships.filter(is_deleted=False).order_by('pk').values('pk', 'predecessor_id', 'successor_id', 'relationship_type', 'lag_days')))

    def test_audit_failure_rolls_back_relationship_duration_and_policy_changes(self):
        plan = self.preview()
        with patch('apps.planning_intelligence.services.piping_guidance.record_event', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                apply_piping_guidance(self.version, self.owner, preview_token=plan['preview_token'])
        self.version.refresh_from_db()
        self.assertFalse(enabled(self.version))
        self.assertFalse(self.version.relationships.exists())
        self.assertTrue(self.version.activities.get(pk=self.gates['vendor_offers', 'EVENT'].pk).is_milestone)
