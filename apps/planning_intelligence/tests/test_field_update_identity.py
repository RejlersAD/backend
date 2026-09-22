"""A report's approved scope must not change through its writable activity FK."""
from rest_framework.test import APIClient

from ..models import DailyFieldUpdate, PlanningProject, Schedule, ScheduleActivity, ScheduleVersion
from . import test_scheduling_engine as fixture


class FieldUpdateIdentityTests(fixture.ScheduleAPIFixture):
    def test_patch_cannot_rebind_report_to_foreign_project_activity(self):
        own_activity = self.activity('OWN', 1)
        report = DailyFieldUpdate.objects.create(version=self.version, activity=own_activity,
            report_date='2026-08-25', reported_by=self.owner)
        foreign = PlanningProject.objects.create(name='Foreign', created_by=self.outsider)
        schedule = Schedule.objects.create(project=foreign, name='Foreign', code='F', planned_start='2026-08-24')
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        activity = ScheduleActivity.objects.create(version=version, external_id='FOREIGN', name='Foreign', duration_days=1)
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.patch(f'/api/v1/planning-intelligence/daily-field-updates/{report.pk}/',
                                {'activity': activity.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('activity', response.data)
        report.refresh_from_db()
        self.assertEqual((report.activity_id, report.version_id), (own_activity.pk, self.version.pk))

    def test_patch_cannot_rebind_even_to_another_authorized_activity(self):
        first, second = self.activity('FIRST', 1), self.activity('SECOND', 1)
        report = DailyFieldUpdate.objects.create(version=self.version, activity=first,
            report_date='2026-08-25', reported_by=self.owner)
        client = APIClient()
        client.force_authenticate(self.owner)
        path = f'/api/v1/planning-intelligence/daily-field-updates/{report.pk}/'
        rejected = client.patch(path, {'activity': second.pk}, format='json')
        allowed = client.patch(path, {'activity': first.pk, 'notes': 'Corrected evidence note'}, format='json')
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(allowed.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.activity_id, first.pk)
        self.assertEqual(report.notes, 'Corrected evidence note')
