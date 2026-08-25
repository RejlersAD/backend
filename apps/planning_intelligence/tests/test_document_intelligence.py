from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User

from ..models import PlanningFile, PlanningProject
from ..services.document_intelligence import (
    compile_run_intelligence, get_or_run_document_intelligence, profile_document,
    run_document_intelligence,
)


class DocumentIntelligenceFixture(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='intel-owner', email='intel-owner@example.com', password='test')
        self.outsider = User.objects.create_user(username='intel-outsider', email='intel-outsider@example.com', password='test')
        self.project = PlanningProject.objects.create(name='Intelligence Project', created_by=self.owner)

    def source(self, name, category, text):
        return PlanningFile.objects.create(
            project=self.project, category=category, file=f'intelligence/{name}',
            original_filename=name, content_type='text/plain', size_bytes=len(text),
            parse_status='done', extracted_text=text, confidence_score=.9, uploaded_by=self.owner,
        )


class DocumentClassificationAndExtractionTests(DocumentIntelligenceFixture):
    def test_profile_classifies_document_and_flags_declared_mismatch(self):
        file_obj = self.source(
            'scope.txt', 'other',
            'SCOPE OF WORK\nThe Contractor shall prepare the FEED engineering deliverables.',
        )

        profile = profile_document(file_obj)

        self.assertEqual(profile.detected_category, 'sow')
        self.assertIn('category_mismatch', profile.quality_flags)
        self.assertGreater(profile.word_count, 5)
        self.assertEqual(len(profile.checksum_sha256), 64)

    def test_fact_extraction_retains_file_and_line_provenance(self):
        file_obj = self.source(
            'sow.txt', 'sow',
            'Project Name: North Field Upgrade\n'
            'Effective Date: 2026-09-01\n'
            'Contract duration is 12 months.\n'
            'The Contractor shall prepare the P&ID and HAZOP Study.\n'
            'Calendar: 5 working days per week and 8 hours per day.',
        )

        run, intelligence = run_document_intelligence(self.project, user=self.owner)

        self.assertEqual(run.status, 'succeeded')
        self.assertEqual(intelligence['detected_project_name'], 'North Field Upgrade')
        project_fact = run.facts.get(fact_type='project_name', extraction_method='deterministic')
        self.assertEqual(project_fact.source_file_id, file_obj.id)
        self.assertEqual(project_fact.source_locator['line'], 1)
        self.assertIn('Project Name', project_fact.source_excerpt)
        self.assertTrue(run.facts.filter(fact_type='deliverable', value__name='Piping & Instrumentation Diagram (P&ID) - Process').exists())
        self.assertTrue(run.facts.filter(fact_type='hse_study').exists())
        self.assertTrue(run.facts.filter(fact_type='requirement').exists())
        self.assertEqual(run.facts.filter(fact_type='calendar').count(), 2)

    def test_unchanged_source_set_reuses_reviewable_run(self):
        self.source('sow.txt', 'sow', 'Project Name: Reusable Project')

        first, _ = get_or_run_document_intelligence(self.project, user=self.owner)
        second, _ = get_or_run_document_intelligence(self.project, user=self.owner)
        forced, _ = get_or_run_document_intelligence(self.project, user=self.owner, force=True)

        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.id, forced.id)

    def test_explicit_scope_exclusion_is_flagged_against_positive_mention(self):
        self.source(
            'scope.txt', 'sow',
            'Electrical Engineering is identified for interface purposes but is explicitly out of scope.',
        )

        run, _ = run_document_intelligence(self.project, user=self.owner)

        conflict = run.conflicts.get(key='discipline:electrical')
        self.assertEqual(conflict.conflict_type, 'explicit_exclusion')
        self.assertGreaterEqual(len(conflict.fact_ids), 2)


class ConflictReviewWorkflowTests(DocumentIntelligenceFixture):
    def setUp(self):
        super().setUp()
        self.source('sow.txt', 'sow', 'Project Name: Alpha Development\nDuration: 12 months')
        self.source('requirements.txt', 'schedule_requirements', 'Project Name: Beta Development\nDuration: 10 months')
        self.run, self.intelligence = run_document_intelligence(self.project, user=self.owner)

    def test_scalar_conflicts_fail_closed_until_resolved(self):
        self.assertEqual(self.run.conflict_count, 2)
        self.assertIsNone(self.intelligence['detected_project_name'])
        self.assertIsNone(self.intelligence['detected_duration_months'])
        self.assertEqual(len(self.intelligence['open_conflicts']), 2)

    def test_conflict_resolution_confirms_selected_evidence_for_generation(self):
        conflict = self.run.conflicts.get(key='project_name:project_name')
        selected = self.run.facts.get(fact_type='project_name', normalized_value='alpha development')
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/intelligence-conflicts/{conflict.id}/resolve/',
            {'action': 'select_fact', 'selected_fact_id': selected.id}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        compiled = compile_run_intelligence(self.run)
        self.assertEqual(compiled['detected_project_name'], 'Alpha Development')
        selected.refresh_from_db()
        self.assertEqual(selected.status, 'confirmed')

    def test_outsider_cannot_read_or_review_intelligence(self):
        fact = self.run.facts.first()
        client = APIClient()
        client.force_authenticate(self.outsider)

        listed = client.get(f'/api/v1/planning-intelligence/intelligence-facts/?run={self.run.id}')
        reviewed = client.post(
            f'/api/v1/planning-intelligence/intelligence-facts/{fact.id}/review/',
            {'status': 'confirmed'}, format='json',
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['results'], [])
        self.assertEqual(reviewed.status_code, 404)

    def test_planner_can_add_manual_confirmed_fact(self):
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/intelligence-runs/{self.run.id}/add-fact/',
            {'fact_type': 'location', 'key': 'location', 'value': 'Ruwais'}, format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'confirmed')
        self.assertEqual(compile_run_intelligence(self.run)['detected_location'], 'Ruwais')
