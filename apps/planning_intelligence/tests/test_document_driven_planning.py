"""The live generation pipeline must consume documents without template guesses."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project

from ..models import (DocumentIntelligenceRun, IntelligenceFact, PlanningFile, PlanningGeneration,
                      PlanningProject, Schedule, ScheduleVersion, WorkCalendar)
from ..services.document_plan import project_document_plan
from ..services.pipeline import generate_schedule, preview_schedule
from ..services.schedule_materializer import materialize_generation


class DocumentDrivenPlanningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='document-planner', email='document-planner@example.test', password='test')
        self.enterprise = Project.objects.create(code='DOCUMENT-ENGINE', name='Warehouse readiness', owner=self.user)
        self.project = PlanningProject.objects.create(
            enterprise_project=self.enterprise, name='Warehouse readiness', created_by=self.user,
            effective_date=date(2030, 3, 1), planned_end_date=date(2030, 6, 30),
        )

    def source(self, text, **overrides):
        return PlanningFile.objects.create(
            project=self.project, category='reference_schedule',
            file=SimpleUploadedFile('project-facts.csv', text.encode()),
            original_filename='project-facts.csv', content_type='text/csv',
            parse_status='done', extracted_text=text, uploaded_by=self.user, **overrides)

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_preview_uses_source_activities_and_links_without_persisting_a_generation(self, _analyze):
        self.source('ID|Task|Duration (working days)|Predecessor\nA|Inspect racking|3|None\nB|Acceptance|2|A:FS+0d\n')
        before = PlanningGeneration.objects.filter(project=self.project).count()
        preview = preview_schedule(self.project, user=self.user)
        self.assertEqual(preview['activity_count'], 2)
        self.assertEqual(preview['relationship_count'], 1)
        self.assertEqual(preview['configured_workflow_activity_count'], 0)
        self.assertEqual(preview['evidence_policy'], 'document_driven')
        self.assertEqual(preview['applied_dependency_rules'], [])
        self.assertEqual(PlanningGeneration.objects.filter(project=self.project).count(), before)

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={
        'disciplines': {'civil': {'in_scope': True, 'deliverables': ['Imagined foundation package']}},
        'hse_studies': ['Invented safety study'], 'estimated_man_hours': 800,
    })
    def test_generation_does_not_promote_unlocated_intelligence_or_default_manhours(self, _analyze):
        source = self.source('ID|Task|Duration (days)|Predecessor\nA|Test scanner|3|None\nB|Train operator||\n')
        generation = generate_schedule(self.project, user=self.user)
        self.assertEqual([row['name'] for row in generation.activities], ['Test scanner', 'Train operator'])
        self.assertEqual([row['duration_days'] for row in generation.activities], [3, None])
        self.assertIsNone(generation.manhours['grand_total_man_hours'])
        self.assertEqual(generation.manhours['by_discipline'], [])
        self.assertEqual(generation.logic_matrix, [])
        self.assertFalse(any(row.get('workflow_template_code') for row in generation.activities))
        for row in generation.activities:
            self.assertEqual(row['source_references'][0]['file_id'], source.pk)
            self.assertFalse(row['calendar_verified'])

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_missing_evidence_materialization_does_not_create_calendar_schedule_or_version(self, _analyze):
        self.source('Task|Duration (days)\nAcceptance|\n')
        generation = generate_schedule(self.project, user=self.user)
        versions_before = ScheduleVersion.objects.count()
        calendar_before = WorkCalendar.objects.filter(project=self.project).count()
        version, run, issues = materialize_generation(generation, requested_by=self.user)
        self.assertIsNone(version)
        self.assertIsNone(run)
        self.assertTrue(issues)
        self.assertEqual(ScheduleVersion.objects.count(), versions_before)
        self.assertEqual(WorkCalendar.objects.filter(project=self.project).count(), calendar_before)
        self.assertFalse(Schedule.objects.filter(project=self.project).exists())
        generation.refresh_from_db()
        self.assertIsNone(generation.activities[0]['duration_days'])

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_even_known_durations_do_not_materialize_without_verified_calendar(self, _analyze):
        self.source('Task|Duration (calendar days)|Start|Finish\nNotice period|7|2030-04-01|2030-04-07\n')
        generation = generate_schedule(self.project, user=self.user)
        version, run, _issues = materialize_generation(generation, requested_by=self.user)
        self.assertIsNone(version)
        self.assertIsNone(run)
        self.assertEqual(generation.activities[0]['duration_unit'], 'calendar_days')
        self.assertEqual(generation.activities[0]['finish_date'], '2030-04-07')

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_override_names_cannot_add_activities_without_source_evidence(self, _analyze):
        self.source('Task|Duration (days)\nSafety induction|2\n')
        generation = generate_schedule(self.project, user=self.user, overrides={
            'disciplines': {'civil': {'deliverables': ['Invented plan']}},
            'hse_studies': ['Invented workshop'],
        })
        self.assertEqual([row['name'] for row in generation.activities], ['Safety induction'])

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_policy_fingerprint_does_not_reuse_legacy_template_output(self, analyze):
        self.source('Task|Duration (days)\nService test|2\n')
        legacy = PlanningGeneration.objects.create(
            project=self.project, version=1, input_fingerprint='same-input',
            activities=[{'id': 'legacy', 'name': 'Old guessed activity'}], generated_by=self.user)
        first = generate_schedule(self.project, user=self.user, input_fingerprint='same-input')
        second = generate_schedule(self.project, user=self.user, input_fingerprint='same-input')
        self.assertNotEqual(first.pk, legacy.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.version, 2)
        self.assertEqual(analyze.call_count, 1)

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_document_finish_does_not_extend_project_master_dates(self, _analyze):
        self.source('Task|Duration (days)|Finish\nSupplier acceptance|4|2030-07-03\n')
        generation = generate_schedule(self.project, user=self.user)
        self.project.refresh_from_db()
        self.assertEqual(self.project.planned_end_date, date(2030, 6, 30))
        self.assertEqual(generation.activities[0]['finish_date'], '2030-07-03')
        self.assertEqual(generation.intelligence['schedule_engine']['date_authority'], 'source_document')

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_missing_relationship_details_are_evidence_not_executable_fs_links(self, _analyze):
        self.source('ID|Task|Duration (days)|Predecessor\nA|Survey|2|None\nB|Issue report|1|A\n')
        generation = generate_schedule(self.project, user=self.user)
        engine = generation.intelligence['schedule_engine']
        self.assertEqual(generation.logic_matrix, [])
        self.assertEqual(len(engine['unresolved_relationships']), 1)
        self.assertIsNone(engine['unresolved_relationships'][0]['type'])
        self.assertIsNone(engine['unresolved_relationships'][0]['lag'])

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_narrative_only_document_returns_review_gaps_without_a_template_schedule(self, _analyze):
        self.source('Perform the scope safely and follow the applicable procedure.')
        generation = generate_schedule(self.project, user=self.user)
        self.assertEqual(generation.activities, [])
        self.assertEqual(generation.logic_matrix, [])
        self.assertTrue(any(row['code'] == 'source_activities_not_specified' for row in generation.validation))

    def test_prose_facts_require_owned_source_exact_quote_and_grounded_title(self):
        text = 'Required submissions include the Warehouse Evacuation Plan before occupation.'
        source = self.source(text)
        run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', source_file_ids=[source.pk],
            started_at=timezone.now(), finished_at=timezone.now(), requested_by=self.user)
        valid = IntelligenceFact.objects.create(
            run=run, source_file=source, fact_type='deliverable', key='evacuation',
            value={'original_title': 'Warehouse Evacuation Plan'}, confidence=.9,
            extraction_method='ai', source_excerpt=text, source_locator={'line': 1})
        IntelligenceFact.objects.create(
            run=run, source_file=source, fact_type='deliverable', key='unguarded',
            value={'name': 'Invented structural package'}, confidence=.99,
            extraction_method='ai', source_excerpt=text, source_locator={'line': 1})
        IntelligenceFact.objects.create(
            run=run, source_file=source, fact_type='deliverable', key='fabricated-quote',
            value={'name': 'Fabricated dossier'}, confidence=.99,
            extraction_method='ai', source_excerpt='Prepare the Fabricated dossier.', source_locator={'line': 2})
        IntelligenceFact.objects.create(
            run=run, source_file=source, fact_type='deliverable', key='empty-quote',
            value={'name': 'Warehouse Evacuation Plan'}, confidence=.99,
            extraction_method='ai', source_excerpt='', source_locator={})
        other_enterprise = Project.objects.create(code='DOCUMENT-OTHER', name='Other site', owner=self.user)
        other_project = PlanningProject.objects.create(enterprise_project=other_enterprise, name='Other site', created_by=self.user)
        foreign = PlanningFile.objects.create(
            project=other_project, category='sow', file=SimpleUploadedFile('foreign.txt', b'Foreign package'),
            original_filename='foreign.txt', parse_status='done', extracted_text='Foreign package', uploaded_by=self.user)
        IntelligenceFact.objects.create(
            run=run, source_file=foreign, fact_type='deliverable', key='foreign',
            value={'name': 'Foreign package'}, confidence=1,
            extraction_method='ai', source_excerpt='Foreign package', source_locator={'line': 1})
        plan = project_document_plan(self.project, {'document_intelligence_run_id': run.pk})
        self.assertEqual([row['name'] for row in plan['activities']], ['Warehouse Evacuation Plan'])
        self.assertEqual(plan['activities'][0]['source_references'][0]['fact_id'], valid.pk)
        self.assertIsNone(plan['activities'][0]['duration_days'])
        self.assertEqual(plan['activities'][0]['predecessors'], [])

    def test_foreign_intelligence_run_cannot_supply_current_project_activities(self):
        source = self.source('An explicit deliverable is a Warehouse Evacuation Plan.')
        foreign_enterprise = Project.objects.create(code='DOCUMENT-RUN', name='Foreign run', owner=self.user)
        foreign_project = PlanningProject.objects.create(enterprise_project=foreign_enterprise, name='Foreign run', created_by=self.user)
        foreign_run = DocumentIntelligenceRun.objects.create(
            project=foreign_project, status='succeeded', started_at=timezone.now(), requested_by=self.user)
        IntelligenceFact.objects.create(
            run=foreign_run, source_file=source, fact_type='deliverable', key='wrong-run',
            value={'name': 'Warehouse Evacuation Plan'}, confidence=1, extraction_method='ai',
            source_excerpt=source.extracted_text, source_locator={'line': 1})
        plan = project_document_plan(self.project, {'document_intelligence_run_id': foreign_run.pk})
        self.assertEqual(plan['activities'], [])
