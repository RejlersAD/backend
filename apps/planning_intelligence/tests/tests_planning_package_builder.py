"""Regression tests for the explicit saved-analysis to editable-package path."""
import datetime as dt
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from ..models import (
    CalendarException, DocumentIntelligenceRun, IntelligenceFact, PlanningFile, PlanningGeneration,
    PlanningProject, ProjectScheduleConfiguration, Schedule, ScheduleVersion, WorkCalendar, WorkflowStage, WorkflowTemplate,
)
from ..services.document_intelligence import _extraction_source_manifest
from ..services.pipeline import generate_schedule, preview_schedule
from ..services.planning_package import build_planning_package
from ..services.planning_package_request import PlanningPackageRequestError
from ..services.preview_confirmation import review_fingerprint
from ..services.schedule_materializer import materialize_generation


class PlanningPackageBuilderTests(TestCase):
    def setUp(self):
        self.project = PlanningProject.objects.create(name='Saved-source planning', effective_date=dt.date(2026, 9, 28))
        self.source = PlanningFile.objects.create(
            project=self.project, category='sow', file='tests/package.txt', original_filename='package.txt',
            content_type='text/plain', parse_status='done',
            extracted_text='General Design Basis\nGeneral Equipment Layout\nGeneral Inspection Report\n',
        )
        self.run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', started_at=timezone.now(), finished_at=timezone.now(),
            source_file_ids=[self.source.pk], summary={'base_intelligence': {'disciplines': {}},
                'extraction_source_manifest': _extraction_source_manifest([self.source])},
        )
        for title in ['Design Basis', 'Equipment Layout', 'Inspection Report']:
            self.fact(title)
        self.template = WorkflowTemplate.objects.create(
            project=self.project, code='STANDARD_5_STAGE', version=1, name='Controlled workflow', status='active',
        )
        for index, code in enumerate(['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE'], 1):
            WorkflowStage.objects.create(template=self.template, sequence=index, code=code, name=code,
                activity_name_template='{deliverable} - {stage}', duration_days=1,
                relationship_to_previous='FS' if index > 1 else '', progress_weight=20)
        self.configuration = ProjectScheduleConfiguration.objects.create(project=self.project, workflow_template=self.template)

    def fact(self, title, discipline='general', *, status='detected'):
        quote = f'General {title}'
        start = self.source.extracted_text.index(quote)
        return IntelligenceFact.objects.create(
            run=self.run, source_file=self.source, fact_type='deliverable', key=title,
            value={'name': title, 'discipline': discipline}, extraction_method='ai', status=status,
            source_excerpt=quote, source_locator={'character_start': start, 'character_end': start + len(quote)},
        )

    def test_saved_analysis_builds_full_source_only_wbs_stage_network_and_eddr(self):
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', side_effect=AssertionError('AI replay')):
            result = build_planning_package(self.project, self.run)
        self.assertEqual(len(result['activities']), 17)
        self.assertEqual(len(result['wbs']), 5)
        self.assertEqual(len(result['eddr']), 3)
        self.assertGreater(len(result['logic_matrix']), 16)
        self.assertTrue(any(edge['source'] == 'planning_sequence_proposal' for edge in result['logic_matrix']))
        self.assertEqual({row.get('deliverable') for row in result['activities'] if row.get('deliverable')},
                         {'Design Basis', 'Equipment Layout', 'Inspection Report'})
        self.assertFalse(any('Survey' in row['name'] or 'EPC' in row['name'] for row in result['activities']))
        self.assertTrue(all(row['predecessors'] for row in result['activities'][1:]))
        self.assertGreater(result['manhours']['grand_total_man_hours'], 0)
        self.assertEqual(self.run.facts.filter(status='confirmed').count(), 0)
        self.assertEqual(self.project.schedule_bases.count(), 0)
        self.assertEqual(self.project.generation_plans.count(), 0)

    def test_preview_has_real_counts_and_assumptions_without_persistence_or_ai(self):
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', side_effect=AssertionError('AI replay')):
            result = preview_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(result['activity_count'], 17)
        self.assertEqual(result['configured_workflow_activity_count'], 15)
        self.assertEqual(result['deliverable_count'], 3)
        self.assertEqual(result['generation_mode'], 'planning_package')
        self.assertTrue(result['assumptions'])
        self.assertTrue(result['workflow_family_counts'])
        self.assertEqual(PlanningGeneration.objects.count(), 0)
        self.assertEqual(ScheduleVersion.objects.count(), 0)
        self.assertEqual(WorkCalendar.objects.count(), 0)

    def test_generation_is_idempotent_and_preserves_unreviewed_source_status(self):
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', side_effect=AssertionError('AI replay')):
            first = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run, input_fingerprint='same-package')
            second = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run, input_fingerprint='same-package')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PlanningGeneration.objects.count(), 1)
        self.assertEqual(set(self.run.facts.values_list('status', flat=True)), {'detected'})
        self.assertEqual(first.intelligence['schedule_engine']['source_analysis_run_id'], self.run.pk)

    def test_general_or_unknown_disciplines_remain_real_wbs_branches(self):
        fact = self.run.facts.get(key='Design Basis')
        fact.value = {'name': 'Design Basis'}
        fact.save(update_fields=['value'])
        result = build_planning_package(self.project, self.run)
        self.assertTrue(any(row.get('discipline') == 'not_specified' for row in result['wbs']))
        self.assertEqual(sum(row.get('deliverable') == 'Design Basis' for row in result['activities']), 5)

    def test_rejected_facts_do_not_become_planning_scope(self):
        self.run.facts.filter(key='Equipment Layout').update(status='rejected')
        result = build_planning_package(self.project, self.run)
        self.assertEqual(len(result['eddr']), 2)
        self.assertFalse(any(row.get('deliverable') == 'Equipment Layout' for row in result['activities']))

    def test_valid_confirmed_preview_exclusions_remain_excluded(self):
        self.run.summary['preview_confirmation'] = {
            'confirmed_at': timezone.now().isoformat(), 'review_fingerprint': review_fingerprint(self.run),
            'preview': {'disciplines': {'general': {'in_scope': True, 'deliverables': ['Design Basis']}}},
        }
        self.run.save(update_fields=['summary'])
        result = build_planning_package(self.project, self.run)
        self.assertEqual([row['deliverable_name'] for row in result['eddr']], ['Design Basis'])
        self.assertEqual(len(result['intelligence']['schedule_engine']['excluded_inventory']), 2)

    def test_missing_start_and_wrong_project_or_incomplete_run_are_rejected(self):
        self.project.effective_date = None
        with self.assertRaisesRegex(PlanningPackageRequestError, 'start date'):
            build_planning_package(self.project, self.run)
        self.project.effective_date = dt.date(2026, 9, 28)
        self.run.project_id = self.project.pk + 100
        with self.assertRaisesRegex(PlanningPackageRequestError, 'belonging'):
            build_planning_package(self.project, self.run)
        self.run.project_id = self.project.pk
        self.run.status = 'running'
        with self.assertRaisesRegex(PlanningPackageRequestError, 'completed'):
            build_planning_package(self.project, self.run)

    def test_source_changes_are_rejected_without_reanalyzing(self):
        self.source.extracted_text += 'Changed scope'
        self.source.save(update_fields=['extracted_text'])
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', side_effect=AssertionError('AI replay')):
            with self.assertRaisesRegex(PlanningPackageRequestError, 'Source documents changed'):
                generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(PlanningGeneration.objects.count(), 0)

    def test_current_project_name_and_dates_can_change_without_reanalyzing_unchanged_files(self):
        self.project.name = 'Current registered project'
        self.project.effective_date = dt.date(2026, 10, 5)
        self.project.save(update_fields=['name', 'effective_date', 'updated_at'])
        result = preview_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(result['sample_activities'][0]['start_date'], '2026-10-05')

    def test_source_review_race_does_not_persist_a_mixed_generation(self):
        original = build_planning_package
        def competing_review(*args, **kwargs):
            payload = original(*args, **kwargs)
            self.run.facts.filter(key='Equipment Layout').update(status='rejected')
            return payload
        with patch('apps.planning_intelligence.services.planning_package.build_planning_package', side_effect=competing_review):
            with self.assertRaisesRegex(PlanningPackageRequestError, 'Evidence review changed'):
                generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(PlanningGeneration.objects.count(), 0)

    def test_config_race_does_not_persist_a_stale_generation(self):
        original = build_planning_package
        def competing_configuration(*args, **kwargs):
            payload = original(*args, **kwargs)
            ProjectScheduleConfiguration.objects.filter(pk=self.configuration.pk).update(configuration_version=99)
            return payload
        with patch('apps.planning_intelligence.services.planning_package.build_planning_package', side_effect=competing_configuration):
            with self.assertRaisesRegex(PlanningPackageRequestError, 'configuration changed'):
                generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(PlanningGeneration.objects.count(), 0)

    def test_stage_content_race_without_configuration_version_change_is_rejected(self):
        original = build_planning_package
        def competing_stage(*args, **kwargs):
            payload = original(*args, **kwargs)
            self.template.stages.filter(sequence=1).update(duration_days=2)
            return payload
        with patch('apps.planning_intelligence.services.planning_package.build_planning_package', side_effect=competing_stage):
            with self.assertRaisesRegex(PlanningPackageRequestError, 'configuration changed'):
                generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(PlanningGeneration.objects.count(), 0)

    def test_template_change_after_payload_and_before_transaction_is_rejected(self):
        from ..services.pipeline import _generation_payload
        def competing_template(*args, **kwargs):
            payload = _generation_payload(*args, **kwargs)
            WorkflowTemplate.objects.filter(pk=self.template.pk).update(name='Changed template')
            return payload
        with patch('apps.planning_intelligence.services.pipeline._generation_payload', side_effect=competing_template):
            with self.assertRaisesRegex(PlanningPackageRequestError, 'configuration changed'):
                generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(PlanningGeneration.objects.count(), 0)

    def test_same_title_distinct_source_occurrences_keep_separate_wbs_and_eddr(self):
        from ..services.planning_package_sources import build_planning_package_sources
        from ..services.document_plan import source_files
        facts = list(self.run.facts.values())
        scope = build_planning_package_sources(source_files(self.project), facts)
        second = deepcopy(scope['deliverables'][0])
        second['id'] += '-distinct-occurrence'
        scope['deliverables'].append(second)
        with patch('apps.planning_intelligence.services.planning_package_sources.build_planning_package_sources', return_value=scope):
            result = build_planning_package(self.project, self.run)
        self.assertEqual(len(result['eddr']), 4)
        self.assertEqual(len({row['id'] for row in result['activities']}), 22)
        self.assertEqual(len({row['wbs_code'] for row in result['eddr']}), 4)

    def test_explicit_typed_source_links_keep_stage_endpoints_and_citations(self):
        from ..services.planning_package_sources import build_planning_package_sources
        from ..services.document_plan import source_files
        scope = build_planning_package_sources(source_files(self.project), list(self.run.facts.values()))
        by_title = {item['title']: item for item in scope['deliverables']}
        scope['dependencies'] = [{
            'predecessor_id': by_title['Design Basis']['id'], 'successor_id': by_title['Equipment Layout']['id'],
            'type': 'FF', 'lag_days': 2, 'lag_unit': 'working_days',
            'source_fact_ids': [999], 'source_references': [{'file_id': self.source.pk, 'excerpt': 'Explicit synthetic FF relationship'}],
        }]
        with patch('apps.planning_intelligence.services.planning_package_sources.build_planning_package_sources', return_value=scope):
            result = build_planning_package(self.project, self.run)
        source_edge = next(edge for edge in result['logic_matrix'] if edge['source'] == 'document_fact')
        activities = {row['id']: row for row in result['activities']}
        self.assertEqual(source_edge['type'], 'FF')
        self.assertEqual(source_edge['lag_days'], 2)
        self.assertEqual(source_edge['source_fact_ids'], [999])
        self.assertEqual(activities[source_edge['predecessor_id']]['workflow_stage_code'], 'FINAL_ISSUE')
        self.assertEqual(activities[source_edge['successor_id']]['workflow_stage_code'], 'FINAL_ISSUE')

    def test_source_cycles_are_rejected_instead_of_calculated_as_success(self):
        from ..services.planning_package_sources import build_planning_package_sources
        from ..services.document_plan import source_files
        scope = build_planning_package_sources(source_files(self.project), list(self.run.facts.values()))
        first, second = scope['deliverables'][:2]
        scope['dependencies'] = [
            {'predecessor_id': pred['id'], 'successor_id': succ['id'], 'type': 'FS', 'lag_days': 0,
             'lag_unit': 'working_days', 'source_fact_ids': [], 'source_references': []}
            for pred, succ in [(first, second), (second, first)]
        ]
        with patch('apps.planning_intelligence.services.planning_package_sources.build_planning_package_sources', return_value=scope):
            with self.assertRaisesRegex(PlanningPackageRequestError, 'cycle'):
                build_planning_package(self.project, self.run)

    def test_default_template_preview_does_not_create_project_configuration(self):
        self.configuration.delete()
        self.template.project = None
        self.template.is_system = True
        self.template.is_default = True
        self.template.save(update_fields=['project', 'is_system', 'is_default'])
        result = preview_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        self.assertEqual(result['configured_workflow_activity_count'], 15)
        self.assertFalse(ProjectScheduleConfiguration.objects.filter(project=self.project).exists())

    def test_fractional_stage_durations_are_not_silently_truncated(self):
        self.template.stages.filter(sequence=1).update(duration_days=Decimal('1.5'))
        with self.assertRaisesRegex(PlanningPackageRequestError, 'whole working-day'):
            build_planning_package(self.project, self.run)

    def test_unsupported_workflow_activity_types_are_not_silently_converted_to_tasks(self):
        self.template.stages.filter(sequence=1).update(activity_type='level_of_effort')
        with self.assertRaisesRegex(PlanningPackageRequestError, 'level-of-effort'):
            build_planning_package(self.project, self.run)

    def test_explicit_workflow_start_milestone_survives_materialization(self):
        self.template.stages.filter(sequence=1).update(activity_type='start_milestone', duration_days=0)
        generation = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        version, calculation, _issues = materialize_generation(generation)
        self.assertEqual(calculation.status, 'succeeded')
        first = version.activities.filter(metadata__workflow_stage_code='IFR').first()
        self.assertEqual(first.activity_type, 'start_milestone')
        self.assertEqual(first.duration_days, 0)

    def test_existing_calendar_holidays_govern_preview_and_are_snapshotted(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Reviewed calendar',
            working_weekdays=[0, 1, 2, 3, 4], hours_per_day=7, is_default=True)
        CalendarException.objects.create(calendar=calendar, date=dt.date(2026, 9, 28), is_working=False)
        result = build_planning_package(self.project, self.run)
        self.assertEqual(result['activities'][0]['start_date'], '2026-09-29')
        self.assertEqual(result['intelligence']['schedule_engine']['calendar']['id'], calendar.pk)
        self.assertEqual(result['manhours']['basis']['hours_per_day'], 7)

    def test_materialization_preserves_proposal_provenance_and_existing_master_inputs(self):
        old_calendar = WorkCalendar.objects.create(project=self.project, name='Prior calendar',
            working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8)
        master = Schedule.objects.create(project=self.project, code='MASTER', name='Existing master',
            planned_start=dt.date(2026, 1, 1), default_calendar=old_calendar)
        generation = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        version, calculation, issues = materialize_generation(generation)
        master.refresh_from_db()
        self.assertEqual(master.planned_start, dt.date(2026, 1, 1))
        self.assertEqual(master.default_calendar_id, old_calendar.pk)
        self.assertEqual(version.activities.count(), 17)
        row = version.activities.exclude(metadata__duration_source=None).filter(metadata__duration_source='workflow_template').first()
        self.assertIsNotNone(row)
        self.assertTrue(row.metadata['source_references'])
        self.assertEqual(row.metadata['proposal_status'], 'draft')
        self.assertIsNone(row.metadata['source_start'])
        self.assertEqual(version.evidence_input_snapshot['project_start'], '2026-09-28')
        self.assertEqual(version.evidence_input_snapshot['source_analysis_run_id'], self.run.pk)
        self.assertNotIn(version.status, {'approved', 'baselined'})
        self.assertIsNotNone(calculation)
        self.assertEqual(calculation.status, 'succeeded')
        version.refresh_from_db()
        self.assertEqual(version.status, 'calculated')
        self.assertEqual(version.activities.get(external_id='PKG-START').planned_start, self.project.effective_date)
        for activity in generation.activities:
            saved = version.activities.get(external_id=activity['id'])
            self.assertEqual(saved.planned_start.isoformat(), activity['start_date'])
            self.assertEqual(saved.planned_finish.isoformat(), activity['finish_date'])
        self.assertEqual(issues, [])
        replay, _run, _issues = materialize_generation(generation)
        self.assertEqual(replay.pk, version.pk)

    def test_changed_calendar_is_rejected_before_materialization(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Calendar',
            working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8, is_default=True)
        generation = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        calendar.working_weekdays = [0, 1, 2, 3]
        calendar.save(update_fields=['working_weekdays'])
        with self.assertRaisesRegex(PlanningPackageRequestError, 'calendar changed'):
            materialize_generation(generation)
        self.assertEqual(ScheduleVersion.objects.count(), 0)

    def test_an_untrusted_package_marker_cannot_enable_calculation(self):
        from ..models import ScheduleActivity
        from ..services.cpm import calculate_schedule_version, SchedulingError
        from ..services.planning_boundaries import accepted_input_validation
        calendar = WorkCalendar.objects.create(project=self.project, name='Manual calendar',
            working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8)
        schedule = Schedule.objects.create(project=self.project, code='UNTRUSTED', name='Manual schedule',
            planned_start=self.project.effective_date, default_calendar=calendar)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, evidence_input_snapshot={
            'schema': 'planning-package-proposal/1', 'source_analysis_run_id': self.run.pk,
            'project_start': '2026-09-28', 'calendar': {'id': calendar.pk},
        })
        ScheduleActivity.objects.create(version=version, external_id='MANUAL', name='Manual row', duration_days=1)
        readiness = accepted_input_validation(version)
        self.assertFalse(readiness['ready_for_calculation'])
        self.assertEqual(readiness['issues'][0]['code'], 'planning_package_origin_invalid')
        with self.assertRaises(SchedulingError):
            calculate_schedule_version(version)

    def test_editable_duration_changes_invalidate_the_successful_calculation_manifest(self):
        from ..services.planning_boundaries import calculation_inputs_current
        generation = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        version, _run, _issues = materialize_generation(generation)
        self.assertTrue(calculation_inputs_current(version))
        activity = version.activities.filter(activity_type='task').first()
        activity.duration_days += 1
        activity.save(update_fields=['duration_days', 'updated_at'])
        self.assertFalse(calculation_inputs_current(version))

    def test_cloned_editable_package_inherits_exact_source_calendar_and_start(self):
        from ..services.master_schedule import _clone
        from ..services.cpm import calculate_schedule_version
        from ..services.planning_package_boundary import package_context, package_origin
        generation = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        version, _run, _issues = materialize_generation(generation)
        clone = _clone(version, None)
        self.assertIsNone(clone.source_generation_id)
        self.assertEqual(package_origin(clone).pk, generation.pk)
        self.assertEqual(clone.evidence_input_snapshot, version.evidence_input_snapshot)
        self.assertEqual(package_context(clone)['start'], self.project.effective_date)
        self.assertEqual(package_context(clone)['calendar'].pk, package_context(version)['calendar'].pk)
        calculation = calculate_schedule_version(clone)
        self.assertEqual(calculation.status, 'succeeded')
        self.assertEqual(clone.activities.get(external_id='PKG-START').planned_start, self.project.effective_date)

    def test_deleted_source_preserves_editable_draft_but_blocks_approval(self):
        from ..services.cpm import calculate_schedule_version
        from ..services.planning_boundaries import accepted_input_validation
        generation = generate_schedule(self.project, mode='planning_package', intelligence_run=self.run)
        version, _run, _issues = materialize_generation(generation)
        self.source.is_deleted = True
        self.source.save(update_fields=['is_deleted', 'updated_at'])
        readiness = accepted_input_validation(version)
        self.assertTrue(readiness['ready_for_calculation'])
        self.assertFalse(readiness['ready_for_approval'])
        self.assertFalse(readiness['ready_for_export'])
        self.assertIn('planning_package_source_changed', {row['code'] for row in readiness['issues']})
        self.assertEqual(calculate_schedule_version(version).status, 'succeeded')
