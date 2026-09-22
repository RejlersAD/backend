"""Executable planner rules preserve the immutable printed source schedule."""
from copy import deepcopy
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from apps.rbac.route_guard import secure_module_endpoints
from ..models import PlanningFile, ScheduleActivity, ScheduleVersion
from ..services.document_plan import project_document_plan
from ..services.planning_boundaries import accepted_input_validation, calculation_inputs_current
from ..services.source_schedule_import import imported_snapshot
from ..services.source_schedule_logic import verified_logic_payload
from ..simple_planning_views import SimplePlanningView
from . import test_source_schedule_import as fixture

urlpatterns = [path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation)) for operation in
      ('preview-source-import', 'apply-source-import', 'preview-source-logic', 'apply-source-logic', 'calculate', 'submit')]]
secure_module_endpoints(urlpatterns)

CALENDAR = {'working_weekdays': [0, 1, 2, 3, 4], 'hours_per_day': 8, 'timezone': 'Asia/Dubai',
            'exceptions': [], 'origin': 'scenario_assumption'}


@override_settings(ROOT_URLCONF=__name__)
class SourceScheduleLogicTests(TestCase):
    def setUp(self):
        fixture.SourceScheduleImportTests.setUp(self)
        self.project.planned_end_date = '2026-11-20'
        self.project.save(update_fields=['planned_end_date'])
        self.schedule.extracted_text = (
            'ID|Task|Duration (working days)|Start|Finish|Total Float (days)|Activity Type\n'
            'P1|Printed project|99|2026-11-06|2026-11-20|0|Project Summary\n'
            'W1|Drawing|50|2026-11-09|2026-11-16|12|WBS Summary\n'
            'A1|Drawing - IFR|2|2026-11-09|2026-11-10|12|Task\n'
            'A2|Drawing - COMPANY REVIEW|2|2026-11-10|2026-11-11|12|Task\n'
            'A3|Drawing - IFA|1|2026-11-12|2026-11-12|12|Task\n'
            'A4|Drawing - COMPANY APPROVAL|1|2026-11-13|2026-11-13|12|Task\n'
            'A5|Drawing - IFT/IFM|1|2026-11-16|2026-11-16|12|Task\n'
            'M1|Project complete|0||2026-11-20|0|Finish Milestone\n')
        self.schedule.save(update_fields=['extracted_text'])
        plan = project_document_plan(self.project)
        hierarchy = {'P1': (1, None, 0), 'W1': (2, 1, 1), 'M1': (8, 1, 1),
                     **{f'A{index}': (index + 2, 2, 2) for index in range(1, 6)}}
        for evidence in [*plan['source_summaries'], *plan['source_project_summaries'],
                         *[row['source_evidence'] for row in plan['activities']]]:
            number, parent, level = hierarchy[evidence['activity_id']]
            evidence['source_hierarchy'] = {'row_number': number, 'parent_row_number': parent, 'level': level,
                                           'basis': 'printed_pdf_indentation'}
        with patch('apps.planning_intelligence.services.source_schedule_import.project_document_plan', return_value=plan):
            preview = self.post('preview-source-import', {'source_file_id': self.schedule.pk, 'master_revision': 0})
            imported = self.post('apply-source-import', {'proposal_token': preview['proposal_token'],
                'reason': 'Preserve the printed schedule.', 'acknowledge_scope': True})
        self.source = ScheduleVersion.objects.get(pk=imported['schedule_version_id'])
        self.source_before = deepcopy(self.source.evidence_input_snapshot)

    def post(self, operation, data, status=200):
        response = self.client.post(self.url + operation + '/', data, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    def preview(self, **changes):
        self.project.refresh_from_db()
        return self.post('preview-source-logic', {'source_version_id': self.source.pk,
            'revision': self.project.master_schedule_revision, 'calendar_spec': CALENDAR,
            'reason': 'Apply named stage rules using an explicit draft calendar.', **changes})

    def apply(self, preview=None, status=200):
        preview = preview or self.preview()
        return self.post('apply-source-logic', {'preview_token': preview['preview_token'],
            'reason': 'Create the calculated planning revision.'}, status)

    def test_exact_stage_links_calculate_new_revision_and_keep_original_source(self):
        with CaptureQueriesContext(connection) as queries:
            preview = self.preview()
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(preview['summary']['relationship_count'], 4)
        self.assertEqual(preview['summary']['unsequenced_activity_count'], 1)
        created = self.apply(preview)
        version = ScheduleVersion.objects.select_related('schedule').get(pk=created['schedule_version_id'])
        self.assertNotEqual(version.schedule_id, self.source.schedule_id)
        self.assertEqual(version.parent_version_id, self.source.pk)
        self.assertEqual(version.activities.count(), 6)
        self.assertEqual(version.wbs_nodes.count(), 2)
        self.assertEqual(version.relationships.count(), 4)
        self.assertTrue(calculation_inputs_current(version))
        self.assertTrue(accepted_input_validation(version)['ready_for_calculation'])
        self.assertFalse(accepted_input_validation(version)['ready_for_approval'])
        self.assertFalse(accepted_input_validation(version)['ready_for_export'])
        state = self.client.get(self.url).data
        by_code = {row['activity_code']: row for row in state['tasks']}
        self.assertEqual(by_code['A2']['source_start_date'], '2026-11-10')
        self.assertEqual(by_code['A2']['planned_start_date'], '2026-11-11')
        self.assertEqual(by_code['A5']['planned_finish_date'], '2026-11-17')
        self.assertEqual(by_code['M1']['planned_finish_date'], '2026-11-20')
        self.assertTrue(by_code['M1']['is_critical'])
        self.assertEqual(by_code['M1']['total_float_days'], 0)
        self.assertEqual(by_code['A2']['source_total_float_days'], 12)
        self.assertTrue(all(row['calculated'] for row in state['tasks']))
        self.assertEqual(state['project_summary']['duration_days'], 10)
        self.assertNotIn('original_duration_days', state['project_summary'])
        self.assertEqual(state['source_project_summary']['duration_days'], 99)
        self.assertEqual(state['source_logic']['summary']['source_duration_days'], 99)
        self.assertEqual(state['source_logic']['summary']['calculated_duration_days'], 10)
        self.assertEqual(state['source_logic']['summary']['source_total_float_days'], 0)
        self.assertEqual(state['source_logic']['summary']['calculated_total_float_days'], 0)
        self.assertFalse(state['calendar']['source_verified'])
        self.assertFalse(state['stale_inputs'])
        self.assertFalse(state['permissions']['can_submit'])
        self.assertFalse(state['permissions']['can_build_source_logic'])
        self.source.refresh_from_db()
        self.assertEqual(self.source.evidence_input_snapshot, self.source_before)
        self.assertIsNotNone(imported_snapshot(self.source))
        self.assertFalse(self.source.calculated_at)
        self.assertEqual(self.source.relationships.count(), 0)
        self.assertFalse(self.apply(preview)['created'])

    def test_changed_source_invalidates_preview_and_unauthorized_user_cannot_apply(self):
        preview = self.preview()
        self.client.force_authenticate(self.reviewer)
        self.apply(preview, status=403)
        self.client.force_authenticate(self.owner)
        PlanningFile.objects.filter(pk=self.schedule.pk).update(extracted_text=self.schedule.extracted_text + '\nRevised')
        self.apply(preview, status=409)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_signature_and_structural_input_tampering_never_enable_calculation(self):
        preview = self.preview()
        self.apply({**preview, 'preview_token': preview['preview_token'] + 'x'}, status=409)
        result = self.apply(preview)
        version = ScheduleVersion.objects.select_related('schedule').get(pk=result['schedule_version_id'])
        self.assertIsNotNone(verified_logic_payload(version))
        first = version.activities.order_by('sort_order').first()
        ScheduleActivity.objects.filter(pk=first.pk).update(duration_days=8)
        self.assertIsNone(verified_logic_payload(version))
        self.assertFalse(accepted_input_validation(version)['ready_for_calculation'])
        self.assertFalse(calculation_inputs_current(version))
        self.apply(preview, status=409)

    def test_calendar_assumption_must_be_explicit_and_partial_days_rejected(self):
        for calendar in ({**CALENDAR, 'origin': 'source_document'}, {**CALENDAR, 'working_weekdays': []},
                         {**CALENDAR, 'exceptions': [{'date': '2026-11-10', 'is_working': True, 'working_hours': 4}]}):
            with self.subTest(calendar=calendar):
                self.project.refresh_from_db()
                self.post('preview-source-logic', {'source_version_id': self.source.pk,
                    'revision': self.project.master_schedule_revision, 'calendar_spec': calendar, 'reason': 'Test'}, status=409)

    def test_second_declared_scenario_keeps_first_revision_and_calendar_intact(self):
        from ..services.master_schedule import select_master_version
        first = self.apply()
        first_version = ScheduleVersion.objects.select_related('schedule').get(pk=first['schedule_version_id'])
        self.project.refresh_from_db()
        select_master_version(self.project, self.owner, revision=self.project.master_schedule_revision, version_id=self.source.pk)
        second = self.apply(self.preview(calendar_spec={**CALENDAR, 'working_weekdays': [0, 1, 2, 3, 4, 5]}))
        second_version = ScheduleVersion.objects.select_related('schedule').get(pk=second['schedule_version_id'])
        self.assertNotEqual(first_version.schedule.default_calendar_id, second_version.schedule.default_calendar_id)
        first_version.refresh_from_db()
        self.assertIsNotNone(verified_logic_payload(first_version))
        self.assertTrue(calculation_inputs_current(first_version))
        self.assertIsNotNone(imported_snapshot(self.source))

    def test_calendar_relationship_and_wbs_tampering_invalidates_signed_inputs(self):
        result = self.apply()
        version = ScheduleVersion.objects.select_related('schedule__default_calendar').get(pk=result['schedule_version_id'])
        calendar = version.schedule.default_calendar
        calendar.working_weekdays = [0, 1, 2, 3, 4, 5]
        calendar.save(update_fields=['working_weekdays'])
        self.assertIsNone(verified_logic_payload(version))
        calendar.working_weekdays = [0, 1, 2, 3, 4]
        calendar.save(update_fields=['working_weekdays'])
        link = version.relationships.first()
        link.lag_days = 1
        link.save(update_fields=['lag_days'])
        self.assertIsNone(verified_logic_payload(version))
        link.lag_days = 0
        link.save(update_fields=['lag_days'])
        node = version.wbs_nodes.first()
        node.name = 'Different source structure'
        node.save(update_fields=['name'])
        self.assertIsNone(verified_logic_payload(version))
