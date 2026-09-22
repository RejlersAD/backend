"""A Gantt edit must preserve the displayed schedule, not replace it with the MDR draft."""
from copy import deepcopy
from datetime import date
from time import monotonic

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ..models import (ActivityRelationship, Schedule, ScheduleActivity, ScheduleVersion,
                      ScheduleWBSNode, WorkCalendar)
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_gantt_selected_edits')
class LargeScheduleGanttTests(TestCase):
    setUp = fixture.SimplePlanningTests.setUp
    read = fixture.SimplePlanningTests.read

    def test_sparse_edits_preserve_1048_activities_222_groups_and_independent_draft(self):
        independent = {'state': 'review', 'revision': 1, 'tasks': [
            fixture.SimplePlanningTests.task(self, key=f'mdr-{index}') for index in range(220)],
            'disciplines': [{'code': 'testing', 'name': 'Testing'}]}
        self.project.simple_planning_state = deepcopy(independent)
        calendar = WorkCalendar.objects.create(project=self.project, name='Project calendar',
            working_weekdays=[0, 1, 2, 3, 4], is_default=True)
        schedule = Schedule.objects.create(project=self.project, code='MASTER', name='Full project schedule',
            planned_start=date(2026, 11, 6), default_calendar=calendar, created_by=self.owner)
        original = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=self.owner)
        groups = ScheduleWBSNode.objects.bulk_create([ScheduleWBSNode(version=original,
            code=f'WBS-{index}', name=f'Deliverable {index}', sort_order=index) for index in range(222)])
        for node in groups[1:]:
            node.parent = groups[0]
            node.level = 1
        ScheduleWBSNode.objects.bulk_update(groups[1:], ['parent', 'level'])
        rows = ScheduleActivity.objects.bulk_create([ScheduleActivity(version=original,
            external_id=f'GEN_{index:04}', name=f'Activity {index}', duration_days=5,
            wbs_node=groups[index % 222], calendar=calendar, sort_order=index,
            metadata={'source_activity_id': f'GEN_{index:04}', 'source_row_number': index + 1,
                      'source_evidence': {'values': {'original_duration_days': 5},
                        'source_references': [{'filename': 'original-schedule.pdf', 'locator': {'row': index + 1}}]}})
            for index in range(1048)])
        ActivityRelationship.objects.bulk_create([ActivityRelationship(version=original,
            predecessor=rows[index], successor=rows[index + 1], relationship_type=['FS', 'SS', 'FF', 'SF'][index % 4],
            lag_days=index % 3, metadata={'source': 'source_document', 'status': 'confirmed'}) for index in range(784)])
        self.project.master_schedule_version = original
        self.project.master_schedule_revision = 2
        self.project.save(update_fields=['simple_planning_state', 'master_schedule_version', 'master_schedule_revision'])
        original_values = list(original.activities.order_by('external_id').values())
        original_links = list(original.relationships.order_by('pk').values())
        state = self.read()
        self.assertEqual(len(state['tasks']), 1048)
        self.assertTrue(state['permissions']['can_edit_gantt'])
        started = monotonic()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url + 'edit-activity/', {'revision': state['revision'],
                'task_id': 'GEN_0500', 'duration_days': 7}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        # Protect against per-activity database reads on every cell save.
        self.assertLess(len(queries), 1200)
        print(f'Large Gantt first edit: {len(queries)} queries, {monotonic() - started:.2f}s')
        saved = response.data
        self.project.refresh_from_db()
        edited = self.project.master_schedule_version
        self.assertNotEqual(edited.pk, original.pk)
        self.assertEqual(edited.activities.count(), 1048)
        self.assertEqual(edited.wbs_nodes.count(), 222)
        self.assertEqual(edited.relationships.filter(is_deleted=False).count(), 784)
        self.assertEqual(edited.wbs_nodes.filter(parent__isnull=False).count(), 221)
        self.assertEqual(self.project.simple_planning_state, independent)
        self.assertEqual(list(original.activities.order_by('external_id').values()), original_values)
        self.assertEqual(list(original.relationships.order_by('pk').values()), original_links)
        self.assertEqual(edited.activities.get(external_id='GEN_0500').duration_days, 7)
        self.assertEqual(edited.activities.get(external_id='GEN_0501').duration_days, 5)
        self.assertEqual(edited.activities.get(external_id='GEN_0500').metadata['source_evidence'],
                         rows[500].metadata['source_evidence'])
        started = monotonic()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(self.url + 'edit-activity/', {'revision': saved['revision'],
                'task_id': 'GEN_0500', 'timing_edit': {'field': 'start', 'value': '2026-11-10'}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertLess(len(queries), 800)
        print(f'Large Gantt subsequent edit: {len(queries)} queries, {monotonic() - started:.2f}s')
        self.assertEqual(response.data['version_id'], edited.pk)
        self.assertEqual(schedule.versions.count(), 2)
        reloaded = next(row for row in self.read()['tasks'] if row['id'] == 'GEN_0500')
        self.assertEqual(reloaded['duration_days'], 7)
        self.assertEqual(reloaded['planned_start_date'], '2026-11-10')
        self.assertIsNone(reloaded['total_float_days'])
        self.assertEqual(edited.relationships.filter(is_deleted=False).count(), 784)
