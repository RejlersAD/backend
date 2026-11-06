"""Recovery changes draft planning inputs, never source identity or contract dates."""
import datetime as dt
from rest_framework.test import APIClient
from unittest.mock import patch

from ..models import ActivityAssignment, PlanningAuditEvent, PlanningGeneration, ScheduleResource
from ..services.cpm import calculate_schedule_version
from ..services.operational_jobs import canonical_fingerprint, schedule_state_fingerprint
from ..services.planner_recovery import recover_planner_draft
from ..services.schedule_approval import ScheduleApprovalError
from ..services.trustworthy_scheduling import run_schedule_assurance
from .test_scheduling_engine import ScheduleAPIFixture


class PlannerRecoveryTests(ScheduleAPIFixture):
    def recover(self, operation):
        self.version.refresh_from_db()
        return recover_planner_draft(self.version, self.owner, operation=operation,
                                     expected_updated_at=self.version.updated_at)

    def test_unadjusted_version_keeps_existing_fingerprint_shape(self):
        self.activity('A', 1)
        with patch('apps.planning_intelligence.services.operational_jobs.canonical_fingerprint',
                   wraps=canonical_fingerprint) as encoder:
            schedule_state_fingerprint(self.version)
        inputs = encoder.call_args.args[0]
        self.assertEqual(inputs['operation'], 'calculate-v5-evidence-boundary')
        self.assertNotIn('planner_adjustments', inputs)

    def test_planning_target_uses_forecast_not_negative_activity_count(self):
        self.project.planned_end_date = dt.date(2026, 8, 24)
        self.project.save()
        a, b = self.activity('A', 3), self.activity('B', 2)
        self.link(a, b)
        calculate_schedule_version(self.version)
        self.version.refresh_from_db()
        before = schedule_state_fingerprint(self.version)
        self.assertEqual(self.version.activities.filter(total_float_days__lt=0).count(), 2)
        result = self.recover('extend_planning_target')
        self.assertEqual(result['extension_calendar_days'], 4)
        self.assertEqual(result['planning_target_finish'], '2026-08-28')
        self.project.refresh_from_db()
        self.assertEqual(self.project.planned_end_date, dt.date(2026, 8, 24))
        self.version.refresh_from_db()
        self.assertNotEqual(schedule_state_fingerprint(self.version), before)
        calculate_schedule_version(self.version)
        self.assertFalse(self.version.activities.filter(total_float_days__lt=0).exists())
        self.assertTrue(PlanningAuditEvent.objects.filter(action='schedule.extend_planning_target').exists())
        self.assertFalse(self.recover('extend_planning_target')['changed'])

    def test_planning_target_recovery_calculates_an_unfinished_draft(self):
        self.project.planned_end_date = dt.date(2026, 8, 24)
        self.project.save()
        a, b = self.activity('A', 3), self.activity('B', 2)
        self.link(a, b)

        result = self.recover('extend_planning_target')

        self.assertTrue(result['changed'])
        self.assertEqual(result['planning_target_finish'], '2026-08-28')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'draft')
        self.assertIsNone(self.version.calculated_at)
        self.assertTrue(self.version.calculation_runs.filter(status='succeeded').exists())

    def test_assurance_requires_current_calculation_inputs(self):
        self.activity('A', 1)
        calculate_schedule_version(self.version)
        client = APIClient()
        client.force_authenticate(self.owner)

        with patch('apps.planning_intelligence.services.planning_boundaries.calculation_inputs_current', return_value=False):
            response = client.post(
                f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/run-assurance/',
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'calculation_inputs_stale')

    def test_numeric_ids_preserve_relationships_and_original_identities(self):
        a, b = self.activity('PKG-A-100', 1), self.activity('PKG-B-100', 1)
        link = self.link(a, b)
        result = self.recover('renumber_activity_ids')
        a.refresh_from_db()
        b.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual((a.external_id, b.external_id), ('100', '110'))
        self.assertEqual((link.predecessor_id, link.successor_id), (a.pk, b.pk))
        self.assertEqual(a.metadata['original_external_id'], 'PKG-A-100')
        self.assertEqual(b.metadata['original_external_id'], 'PKG-B-100')
        self.assertTrue(result['changed'])
        self.assertFalse(self.recover('renumber_activity_ids')['changed'])

    def test_numeric_reordering_handles_existing_numeric_collisions(self):
        a, b = self.activity('110', 1), self.activity('100', 1)
        self.recover('renumber_activity_ids')
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.external_id, b.external_id), ('100', '110'))

    def test_generated_logic_can_be_restored_after_numeric_renumbering(self):
        a, b = self.activity('PKG-A-100', 1), self.activity('PKG-B-100', 1)
        self.version.source_generation = PlanningGeneration.objects.create(
            project=self.project, version=1, intelligence={'schedule_engine': {'policy': 'planning_package'}},
            logic_matrix=[{'predecessor_id': a.external_id, 'successor_id': b.external_id, 'type': 'FS', 'lag_days': 0}],
        )
        self.version.save()
        self.recover('renumber_activity_ids')
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.post(f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/rebuild-generated-logic/')
        self.assertEqual(response.status_code, 200, response.data)
        link = self.version.relationships.get(is_deleted=False)
        self.assertEqual((link.predecessor_id, link.successor_id), (a.pk, b.pk))

    def test_resource_quality_is_advisory_and_preserves_capacity(self):
        a, b = self.activity('A', 1), self.activity('B', 1)
        resource = ScheduleResource.objects.create(project=self.project, name='Engineer', code='ENG', capacity_units_per_day=8)
        for row in (a, b):
            ActivityAssignment.objects.create(activity=row, resource=resource, planned_units=8)
        calculate_schedule_version(self.version)
        assurance = run_schedule_assurance(self.version)
        self.assertEqual(assurance.status, 'ready')
        self.assertFalse(assurance.blockers)
        self.assertTrue(any(row['code'] == 'resource_overallocation' for row in assurance.warnings))
        resource.refresh_from_db()
        self.assertEqual(resource.capacity_units_per_day, 8)

    def test_immutable_stale_and_unauthorized_adjustments_are_rejected(self):
        self.activity('A', 1)
        initial = self.version.updated_at
        self.version.change_summary = 'Concurrent edit'
        self.version.save()
        with self.assertRaises(ScheduleApprovalError) as error:
            recover_planner_draft(self.version, self.owner, operation='renumber_activity_ids', expected_updated_at=initial)
        self.assertEqual(error.exception.payload['code'], 'version_conflict')
        with self.assertRaises(ScheduleApprovalError):
            recover_planner_draft(self.version, self.outsider, operation='renumber_activity_ids', expected_updated_at=self.version.updated_at)
        self.version.status = 'baselined'
        self.version.save()
        with self.assertRaises(ScheduleApprovalError):
            self.recover('renumber_activity_ids')

    def test_api_recovery_uses_existing_permission_and_revision_checks(self):
        self.activity('PKG-A-100', 1)
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.patch(f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/recover-draft/',
                                    {'operation': 'renumber_activity_ids', 'expected_updated_at': self.version.updated_at.isoformat()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.version.activities.get().external_id, '100')
