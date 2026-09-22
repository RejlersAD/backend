"""Source import must make the timeline useful without claiming executable CPM."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
import time

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from apps.core.project_models import ProjectMember, ProjectTask
from apps.rbac.route_guard import secure_module_endpoints
from ..models import PlanningFile, PlanningProject, ScheduleActivity, ScheduleVersion, ScheduleWBSNode
from ..simple_planning_views import SimplePlanningView
from ..services.planning_boundaries import accepted_input_validation
from ..services.source_schedule_import import imported_snapshot
from . import test_source_schedule_preview as fixture


urlpatterns = [
    path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation)) for operation in (
               'source-preview', 'preview-source-import', 'apply-source-import', 'select-version', 'calculate', 'submit')],
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class SourceScheduleImportTests(TestCase):
    def setUp(self):
        fixture.SourceSchedulePreviewTests.setUp(self)

    def post(self, operation, data, status=200):
        response = self.client.post(self.url + operation + '/', data, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    def preview(self, **changes):
        self.project.refresh_from_db()
        return self.post('preview-source-import', {
            'source_file_id': self.schedule.pk, 'master_revision': self.project.master_schedule_revision, **changes})

    def apply(self, preview=None, *, status=200, **changes):
        preview = preview or self.preview()
        return self.post('apply-source-import', {'proposal_token': preview['proposal_token'],
            'reason': 'Use this reviewed source as the project schedule.', 'acknowledge_scope': True, **changes}, status)

    def read(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_preview_is_read_only_and_requires_source_scope_confirmation(self):
        with CaptureQueriesContext(connection) as queries:
            preview = self.preview()
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['summary']['activity_count'], 2)
        self.assertEqual(preview['summary']['relationship_count'], 1)
        self.assertEqual(preview['summary']['start_date'], '2026-11-09')
        self.assertEqual(preview['summary']['finish_date'], '2026-11-16')
        self.assertEqual(preview['summary']['unmapped_register_count'], 2)
        self.assertTrue(preview['summary']['prior_work_preserved'])
        self.assertEqual(self.apply(preview, acknowledge_scope=False, status=409)['code'], 'source_import_scope_required')
        self.apply(preview, reason='', status=400)
        self.assertFalse(self.project.schedules.exists())

    def test_apply_selects_source_dates_and_exact_links_without_replacing_mdr_or_assignments(self):
        original = {'revision': 12, 'tasks': [{'id': 'assigned', 'title': 'Training', 'duration_days': None,
                    'discipline': 'operations', 'depends_on': [], 'assignee_id': self.reviewer.pk}]}
        self.project.simple_planning_state = deepcopy(original)
        self.project.save(update_fields=['simple_planning_state'])
        assignment = ProjectTask.objects.create(project=self.enterprise, title='Training', assigned_to=self.reviewer,
            source_key=f'simple:{self.project.pk}:assigned', status='in_progress', progress_percent=40)
        result = self.apply()
        self.project.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, original)
        self.assertEqual(assignment.status, 'in_progress')
        self.assertEqual(assignment.progress_percent, 40)
        self.assertEqual(assignment.assigned_to_id, self.reviewer.pk)
        self.assertEqual(str(self.project.effective_date), '2026-11-06')
        self.assertEqual(str(self.project.planned_end_date), '2026-12-20')
        self.assertEqual(self.project.master_schedule_version_id, result['schedule_version_id'])
        version = self.project.master_schedule_version
        self.assertEqual(version.activities.count(), 2)
        self.assertEqual(version.relationships.count(), 1)
        self.assertFalse(version.calculated_at)
        self.assertFalse(version.schedule.default_calendar_id)
        self.assertFalse(self.project.work_calendars.exists())
        self.assertEqual(len(imported_snapshot(version)['register_inventory']), 2)
        state = self.read()
        first, second = state['tasks']
        self.assertEqual((first['activity_code'], first['duration_days']), ('D1', 4))
        self.assertEqual(first['source_start_date'], '2026-11-09')
        self.assertEqual(first['source_finish_date'], '2026-11-12')
        self.assertIsNone(first['planned_start_date'])
        self.assertIsNone(first['total_float_days'])
        self.assertFalse(first['calculated'])
        self.assertEqual(first['field_provenance']['duration_days']['type'], 'document')
        self.assertEqual(first['field_provenance']['duration_days']['status'], 'extracted_requires_review')
        self.assertEqual(second['dependency_details'][0]['task_id'], first['id'])
        self.assertEqual(second['dependency_details'][0]['lag_unit'], 'days')
        self.assertFalse(state['permissions']['can_calculate'])
        self.assertFalse(state['permissions']['can_submit'])
        self.assertTrue(state['source_import']['prior_work_preserved'])
        self.assertFalse(state['stale_inputs'])
        self.assertEqual(state['calendar']['name'], 'Not Specified')
        self.assertFalse(accepted_input_validation(version)['ready_for_calculation'])
        self.assertFalse(accepted_input_validation(version)['ready_for_approval'])
        self.assertFalse(accepted_input_validation(version)['ready_for_export'])
        self.post('calculate', {'revision': state['revision']}, status=409)
        self.post('submit', {'revision': state['revision']}, status=409)

    def test_import_freshness_tracks_source_changes_separately_from_cpm_readiness(self):
        self.apply()
        fresh = self.read()
        self.assertFalse(fresh['stale_inputs'])
        self.assertFalse(fresh['accepted_input_readiness']['ready_for_calculation'])
        self.assertFalse(fresh['permissions']['can_calculate'])
        self.assertFalse(fresh['permissions']['can_submit'])
        # A bulk text edit must invalidate freshness even without updated_at.
        PlanningFile.objects.filter(pk=self.schedule.pk).update(
            extracted_text=self.schedule.extracted_text.replace('Inspection|4|', 'Inspection|5|'))
        changed = self.read()
        self.assertTrue(changed['stale_inputs'])
        self.assertEqual(changed['tasks'][0]['duration_days'], 4)
        for permission in ('can_calculate', 'can_submit', 'can_approve_publish'):
            self.assertEqual(changed['permissions'][permission], fresh['permissions'][permission])
        self.assertFalse(changed['permissions']['can_build_source_logic'])
        self.assertEqual(changed['accepted_input_readiness'], fresh['accepted_input_readiness'])

    def test_explicit_milestone_signed_lag_and_calendar_day_quantity_are_preserved(self):
        self.schedule.extracted_text = ('ID|Task|Duration|Duration Unit|Start|Finish|Predecessors|Activity Type\n'
            'M1|Notice to proceed|0|calendar days|2026-11-09|2026-11-09|None|Start Milestone\n'
            'A2|Wait period|2.5|calendar days|2026-11-10|2026-11-13|M1:SS-1.5d|Task\n')
        self.schedule.save(update_fields=['extracted_text'])
        result = self.apply()
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        milestone, task = list(version.activities.order_by('sort_order'))
        self.assertEqual(milestone.activity_type, 'start_milestone')
        self.assertEqual(task.duration_days, Decimal('2.5'))
        self.assertEqual(task.metadata['duration_unit'], 'calendar_days')
        link = version.relationships.get()
        self.assertEqual(link.relationship_type, 'SS')
        self.assertEqual(link.lag_days, Decimal('-1.5'))
        first, second = self.read()['tasks']
        self.assertTrue(first['is_milestone'])
        self.assertEqual(second['dependency_details'][0]['lag_days'], -1.5)
        self.assertEqual(second['duration_unit'], 'calendar_days')

    def test_explicit_project_summary_keeps_printed_duration_without_recalculating_calendar(self):
        self.schedule.extracted_text = self.schedule.extracted_text.replace(
            'D1|Inspection', 'P1|Approved source scope|165|working days|2026-01-06|2026-09-04|None|Project Summary\nD1|Inspection')
        self.schedule.save(update_fields=['extracted_text'])
        self.apply()
        state = self.read()
        summary = state['project_summary']
        self.assertEqual(summary['duration_days'], 165)
        self.assertEqual(summary['source_start_date'], '2026-01-06')
        self.assertEqual(summary['source_finish_date'], '2026-09-04')
        self.assertEqual(summary['duration_basis'], 'source_document')
        self.assertFalse(summary['duration_calendar_verified'])
        self.assertIsNone(summary['planned_start_date'])
        self.assertIsNone(summary['total_float_days'])
        self.assertEqual(state['project']['start_date'], '2026-11-06')

    def test_import_preserves_printed_float_for_activities_and_project_summary(self):
        self.schedule.extracted_text = (
            'ID|Task|Duration|Duration Unit|Start|Finish|Total Float (days)|Predecessors|Activity Type\n'
            'P1|Project summary|165|working days|2026-01-06|2026-09-04|0|None|Project Summary\n'
            'A1|Design|4|working days|2026-01-06|2026-01-09|-2.5|None|Task\n'
            'A2|Review|2|working days|2026-01-12|2026-01-13|0|A1:FS+0d|Task\n')
        self.schedule.save(update_fields=['extracted_text'])
        preview = self.client.get(self.url + 'source-preview/').data
        self.assertEqual(preview['summary']['total_float_count'], 2)
        self.assertEqual([row['source_total_float_days'] for row in preview['rows']], [-2.5, 0])
        self.apply()
        state = self.read()
        summary = state['project_summary']
        self.assertEqual(summary['duration_days'], 165)
        self.assertEqual(summary['source_start_date'], '2026-01-06')
        self.assertEqual(summary['source_finish_date'], '2026-09-04')
        self.assertEqual(summary['source_total_float_days'], 0)
        self.assertEqual(summary['source_total_float_status'], 'extracted')
        self.assertIsNone(summary['total_float_days'])
        self.assertEqual([row['source_total_float_days'] for row in state['tasks']], [-2.5, 0])
        self.assertTrue(all(row['total_float_days'] is None and not row['calculated'] for row in state['tasks']))
        self.assertTrue(all(row['field_provenance']['source_total_float_days']['type'] == 'document' for row in state['tasks']))

    def geometry_plan(self):
        from ..services.document_plan import project_document_plan
        self.schedule.extracted_text = (
            'ID|Task|Duration|Duration Unit|Start|Finish|Total Float (days)|Activity Type\n'
            'P1|Printed project|165|working days|2026-01-06|2026-09-04|0|Project Summary\n'
            'W1|Printed package|25|working days|2026-02-02|2026-03-06|12|WBS Summary\n'
            'A1|Design|4|working days|2026-02-02|2026-02-05|3|Task\n'
            'A2|Review|2|working days|2026-02-09|2026-02-10|5|Task\n')
        self.schedule.save(update_fields=['extracted_text'])
        plan = project_document_plan(self.project)
        by_id = {'P1': (1, None, 0), 'W1': (2, 1, 1), 'A1': (3, 2, 2), 'A2': (4, 2, 2)}
        for row in [*plan['source_summaries'], *plan['source_project_summaries'],
                    *[item['source_evidence'] for item in plan['activities']]]:
            number, parent, level = by_id[row['activity_id']]
            row['source_hierarchy'] = {'row_number': number, 'parent_row_number': parent, 'level': level,
                                       'basis': 'printed_pdf_indentation'}
        return plan

    def test_printed_hierarchy_keeps_summary_values_instead_of_child_rollups(self):
        plan = self.geometry_plan()
        with patch('apps.planning_intelligence.services.source_schedule_import.project_document_plan', return_value=plan):
            result = self.apply()
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        project_node, package = list(version.wbs_nodes.order_by('sort_order'))
        self.assertEqual(package.parent_id, project_node.pk)
        self.assertTrue(all(row.wbs_node_id == package.pk for row in version.activities.all()))
        self.assertEqual(version.relationships.count(), 0)
        state = self.read()
        self.assertEqual(state['hierarchy_source'], 'printed_pdf_indentation')
        package_summary = state['wbs_nodes'][1]['summary']
        self.assertEqual(package_summary['duration_days'], 25)
        self.assertEqual(package_summary['source_start_date'], '2026-02-02')
        self.assertEqual(package_summary['source_finish_date'], '2026-03-06')
        self.assertEqual(package_summary['source_total_float_days'], 12)
        self.assertIsNone(package_summary['total_float_days'])
        self.assertEqual(state['project_summary']['duration_days'], 165)
        self.assertEqual(state['wbs_nodes'][0]['source_row_number'], 1)
        self.assertTrue(state['wbs_nodes'][0]['is_source_project'])
        self.assertFalse(state['wbs_nodes'][1]['is_source_project'])
        self.assertNotEqual(state['wbs_nodes'][0]['name'], state['project']['name'])
        self.assertIsNotNone(imported_snapshot(version))
        ScheduleWBSNode.objects.filter(pk=package.pk).update(name='Changed package')
        self.assertIsNone(imported_snapshot(version))

    def test_incomplete_printed_hierarchy_cannot_be_imported_as_a_different_structure(self):
        plan = self.geometry_plan()
        plan['activities'][0]['source_evidence']['source_hierarchy']['parent_row_number'] = 999
        with patch('apps.planning_intelligence.services.source_schedule_import.project_document_plan', return_value=plan):
            preview = self.preview()
        self.assertFalse(preview['can_apply'])
        self.assertIn('source_import_hierarchy_unsupported', {row['code'] for row in preview['warnings']})
        self.assertFalse(self.project.schedules.exists())

    def test_missing_duration_rejects_import_instead_of_storing_zero_or_template(self):
        self.schedule.extracted_text = self.schedule.extracted_text.replace('Inspection|4|', 'Inspection||')
        self.schedule.save(update_fields=['extracted_text'])
        preview = self.preview()
        self.assertFalse(preview['can_apply'])
        self.assertIsNone(preview['proposal_token'])
        self.assertIn('source_import_duration_unsupported', {row['code'] for row in preview['warnings']})
        self.assertFalse(self.project.schedules.exists())

    def test_duration_precision_is_not_rounded(self):
        self.schedule.extracted_text = self.schedule.extracted_text.replace('Inspection|4|', 'Inspection|4.125|')
        self.schedule.save(update_fields=['extracted_text'])
        self.assertFalse(self.preview()['can_apply'])
        self.assertFalse(ScheduleActivity.objects.exists())

    def test_unknown_zero_duration_kind_stays_unknown_without_automatic_milestone(self):
        self.schedule.extracted_text = ('ID|Task|Duration|Duration Unit|Start|Finish\n'
            'Z1|Unclassified zero row|0|working days|2026-11-09|2026-11-09\n')
        self.schedule.save(update_fields=['extracted_text'])
        self.apply()
        task = self.read()['tasks'][0]
        self.assertFalse(task['is_milestone'])
        self.assertIsNone(task['source_activity_type'])
        self.assertEqual(task['depends_on'], [])

    def test_conflicting_explicit_task_and_milestone_flag_stays_unresolved(self):
        from ..services.structured_schedule_evidence import parse_structured_schedule_evidence
        source = parse_structured_schedule_evidence('ID|Task|Duration|Duration Unit|Activity Type|Is Milestone\n'
            'A1|Conflicting source type|0|working days|Task|Yes\n')
        row = source['rows'][0]
        self.assertIsNone(row['values']['is_milestone'])
        self.assertEqual(row['field_status']['is_milestone'], 'conflicting_values')

    def test_get_source_filter_and_import_scope_include_only_selected_file(self):
        other_file = PlanningFile.objects.create(project=self.project, category='reference_schedule',
            file='tests/other.csv', original_filename='Other.csv', parse_status='done', uploaded_by=self.owner,
            extracted_text=self.schedule.extracted_text.replace('D1|Inspection', 'B1|Other activity'))
        response = self.client.get(self.url + 'source-preview/', {'source_file_id': self.schedule.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['can_import'])
        self.assertEqual(response.data['summary']['activity_count'], 2)
        self.assertEqual([row['activity_count'] for row in response.data['source_files']], [0, 2, 2])
        self.assertEqual(response.data['master_revision'], 0)
        self.apply()
        version = self.project.schedules.latest('pk').versions.get()
        self.assertTrue(all(row.metadata['source_references'][0]['file_id'] == self.schedule.pk for row in version.activities.all()))
        self.assertNotEqual(other_file.pk, self.schedule.pk)

    def test_reviewer_with_module_update_cannot_import_and_other_project_is_hidden(self):
        self.client.force_authenticate(self.reviewer)
        self.post('preview-source-import', {'source_file_id': self.schedule.pk, 'master_revision': 0}, status=403)
        self.assertFalse(self.client.get(self.url + 'source-preview/').data['can_import'])
        self.client.force_authenticate(self.other)
        self.post('preview-source-import', {'source_file_id': self.schedule.pk, 'master_revision': 0}, status=404)
        self.client.force_authenticate(self.owner)
        with patch('apps.planning_intelligence.services.source_schedule_import.module_action_allowed', return_value=False):
            self.post('preview-source-import', {'source_file_id': self.schedule.pk, 'master_revision': 0}, status=403)
        self.assertFalse(self.project.schedules.exists())

    def test_token_is_bound_to_actor_and_cannot_be_tampered_with_or_expired(self):
        preview = self.preview()
        self.apply(preview, proposal_token=preview['proposal_token'] + 'x', status=409)
        ProjectMember.objects.create(project=self.enterprise, user=self.other, role='engineer')
        self.client.force_authenticate(self.other)
        self.apply(preview, status=409)
        self.client.force_authenticate(self.owner)
        with patch('django.core.signing.time.time', return_value=time.time() + 7200):
            self.apply(preview, status=409)
        self.assertFalse(self.project.schedules.exists())

    def test_source_text_changes_invalidate_preview_without_timestamp_change(self):
        preview = self.preview()
        PlanningFile.objects.filter(pk=self.schedule.pk).update(extracted_text=self.schedule.extracted_text.replace('Inspection|4|', 'Inspection|5|'))
        self.assertEqual(self.apply(preview, status=409)['code'], 'source_import_stale')
        self.assertFalse(self.project.schedules.exists())

    def test_working_draft_or_project_window_changes_invalidate_preview(self):
        preview = self.preview()
        PlanningProject.objects.filter(pk=self.project.pk).update(simple_planning_state={'revision': 0, 'tasks': [], 'note': 'new'})
        self.apply(preview, status=409)
        preview = self.preview()
        PlanningProject.objects.filter(pk=self.project.pk).update(planned_end_date='2026-12-21')
        self.apply(preview, status=409)
        self.assertFalse(self.project.schedules.exists())

    def test_current_master_rename_invalidates_preview_even_without_updated_at_change(self):
        self.apply()
        preview = self.preview()
        self.project.refresh_from_db()
        activity = self.project.master_schedule_version.activities.first()
        ScheduleActivity.objects.filter(pk=activity.pk).update(name='Changed after review')
        self.apply(preview, status=409)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_apply_is_idempotent_but_replay_cannot_overwrite_later_selection(self):
        preview = self.preview()
        first = self.apply(preview)
        second = self.apply(preview)
        self.assertEqual(first['schedule_version_id'], second['schedule_version_id'])
        self.assertFalse(second['created'])
        self.assertEqual(ScheduleVersion.objects.count(), 1)
        self.project.refresh_from_db()
        self.post('select-version', {'revision': self.project.master_schedule_revision, 'version_id': None})
        self.apply(preview, status=409)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_replay_rejects_changed_imported_rows_and_drops_source_claim(self):
        preview = self.preview()
        applied = self.apply(preview)
        version = ScheduleVersion.objects.get(pk=applied['schedule_version_id'])
        ScheduleActivity.objects.filter(version=version).update(duration_days=10)
        self.assertIsNone(imported_snapshot(version))
        self.apply(preview, status=409)
        self.assertNotIn('source_import', self.read())

    def test_second_explicit_import_retains_prior_version_and_source_snapshot(self):
        first = self.apply()
        old = ScheduleVersion.objects.get(pk=first['schedule_version_id'])
        preserved = deepcopy(old.evidence_input_snapshot)
        second = self.apply()
        self.assertNotEqual(first['schedule_version_id'], second['schedule_version_id'])
        old.refresh_from_db()
        self.assertEqual(old.evidence_input_snapshot, preserved)
        self.assertEqual(old.activities.count(), 2)
        self.assertEqual(old.relationships.count(), 1)
        self.assertEqual(old.status, 'draft')
        self.assertEqual(ScheduleVersion.objects.count(), 2)

    def test_relationship_metadata_edits_invalidate_unchanged_source_claim(self):
        preview = self.preview()
        result = self.apply(preview)
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        link = version.relationships.get()
        link.metadata = {**link.metadata, 'lag_unit': 'hours'}
        link.save(update_fields=['metadata'])
        self.assertIsNone(imported_snapshot(version))
        self.apply(preview, status=409)

    def test_replay_rejects_documents_changed_after_successful_import(self):
        preview = self.preview()
        self.apply(preview)
        PlanningFile.objects.filter(pk=self.schedule.pk).update(extracted_text=self.schedule.extracted_text + '\nNote: revised')
        self.apply(preview, status=409)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_stale_master_revision_and_archived_project_are_rejected(self):
        self.post('preview-source-import', {'source_file_id': self.schedule.pk, 'master_revision': 7}, status=409)
        preview = self.preview()
        self.project.is_deleted = True
        self.project.save(update_fields=['is_deleted'])
        self.apply(preview, status=404)

    def test_source_dates_outside_project_window_are_warned_and_not_shifted(self):
        self.project.effective_date = '2026-11-10'
        self.project.planned_end_date = '2026-11-15'
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        preview = self.preview()
        self.assertTrue(preview['can_apply'])
        self.assertIn('source_import_outside_window', {row['code'] for row in preview['warnings']})
        self.apply(preview)
        state = self.read()
        self.assertEqual(state['tasks'][0]['source_start_date'], '2026-11-09')
        self.assertEqual(state['tasks'][1]['source_finish_date'], '2026-11-16')
        self.project.refresh_from_db()
        self.assertEqual(str(self.project.effective_date), '2026-11-10')
        self.assertEqual(str(self.project.planned_end_date), '2026-11-15')

    def test_repeated_printed_ids_remain_distinct_source_rows(self):
        self.schedule.extracted_text = ('ID|Task|Duration|Duration Unit|Start|Finish\n'
            'A1|First source row|2|working days|2026-11-09|2026-11-10\n'
            'A1|Second source row|3|working days|2026-11-11|2026-11-13\n')
        self.schedule.save(update_fields=['extracted_text'])
        self.apply()
        tasks = self.read()['tasks']
        self.assertEqual(len(tasks), 2)
        self.assertNotEqual(tasks[0]['id'], tasks[1]['id'])
        self.assertEqual([row['activity_code'] for row in tasks], ['A1', 'A1'])
        self.assertTrue(all(not row['depends_on'] for row in tasks))

    def test_foreign_missing_and_unprocessed_source_cannot_be_imported(self):
        self.post('preview-source-import', {'source_file_id': self.schedule.pk + 100000, 'master_revision': 0}, status=404)
        self.assertEqual(self.client.get(self.url + 'source-preview/', {'source_file_id': self.schedule.pk + 100000}).status_code, 404)
        self.schedule.parse_status = 'pending'
        self.schedule.save(update_fields=['parse_status'])
        self.assertFalse(self.preview()['can_apply'])
        self.assertFalse(self.preview(source_file_id=self.register.pk)['can_apply'])
