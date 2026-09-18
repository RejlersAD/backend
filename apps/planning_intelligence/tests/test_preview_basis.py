import datetime
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from ..models import DocumentIntelligenceRun, IntelligenceFact, PlanningFile, PlanningProject
from ..services.preview_confirmation import review_fingerprint, source_fingerprint
from ..services.schedule_basis import apply_approved_basis, build_schedule_basis
from ..services.wbs_generator import build_wbs
from ..services.workable_plan import build_workable_plan


class ConfirmedPreviewBasisTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='confirmed-preview-planner', password='test')
        self.project = PlanningProject.objects.create(
            name='Original project', effective_date=datetime.date(2026, 1, 1),
            planned_end_date=datetime.date(2026, 6, 1), duration_months=5, created_by=self.user,
        )
        self.file = PlanningFile.objects.create(
            project=self.project, category='mdr', original_filename='preview-mdr.txt',
            file=SimpleUploadedFile('preview-mdr.txt', b'Register'), parse_status='done',
            uploaded_by=self.user,
        )
        self.run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', source_file_ids=[self.file.id],
            started_at=timezone.now(), finished_at=timezone.now(), requested_by=self.user,
        )
        for discipline, name, number in [
            ('civil', 'Foundation drawing', 'CIV-001'),
            ('civil', 'Foundation drawing', 'CIV-002'),
            ('civil', 'Inspection report', 'CIV-003'),
            ('mechanical', 'Equipment list', 'MEC-001'),
        ]:
            IntelligenceFact.objects.create(
                run=self.run, source_file=self.file, fact_type='deliverable', key=number,
                value={'name': name, 'discipline': discipline, 'document_number': number, 'document_revision': 'B'},
                normalized_value=name.lower(), confidence=.9, status='confirmed',
            )
        self.hazop = IntelligenceFact.objects.create(
            run=self.run, source_file=self.file, fact_type='hse_study', key='hazop',
            value='HAZOP', normalized_value='hazop', confidence=.9, status='confirmed',
        )
        IntelligenceFact.objects.create(
            run=self.run, source_file=self.file, fact_type='hse_study', key='envid',
            value='ENVID', normalized_value='envid', confidence=.9, status='confirmed',
        )
        self.preview = {
            'detected_project_name': 'Reviewed project', 'detected_effective_date_text': '2026-02-01',
            'detected_duration_months': 4,
            'disciplines': {
                'civil': {'in_scope': True, 'deliverables': ['Foundation drawing', 'Inspection report', 'Planner added report'],
                          'excluded_deliverables': ['Inspection report']},
                'mechanical': {'in_scope': False, 'deliverables': ['Equipment list'], 'excluded_deliverables': []},
                'hse': {'in_scope': True, 'deliverables': [], 'excluded_deliverables': []},
            },
            'hse_studies': ['HAZOP'],
        }

    def confirm(self, preview=None):
        self.run.summary = {
            **self.run.summary,
            'preview_confirmation': {
                'preview': deepcopy(preview if preview is not None else self.preview),
                'confirmed_at': timezone.now().isoformat(), 'confirmed_by': self.user.id,
                'source_fingerprint': source_fingerprint(self.project),
                'review_fingerprint': review_fingerprint(self.run),
            },
        }
        self.run.save(update_fields=['summary', 'updated_at'])

    def approved_basis(self):
        # This suite consumes an existing approval; approval eligibility has a
        # separate permission suite and is not part of confirming a preview.
        basis = build_schedule_basis(self.run)
        basis.status = 'approved'
        basis.save(update_fields=['status'])
        return basis

    def test_saved_preview_controls_new_basis_and_preserves_document_evidence(self):
        self.confirm()
        raw_facts = list(self.run.facts.values_list('id', 'value', 'status'))
        basis = build_schedule_basis(self.run)

        self.assertEqual(basis.project_name, 'Reviewed project')
        self.assertEqual(basis.effective_date, datetime.date(2026, 2, 1))
        self.assertEqual(basis.duration_months, 4)
        self.assertEqual(basis.deliverables.filter(canonical_name='Foundation drawing', status='confirmed').count(), 2)
        first = basis.deliverables.get(document_number='CIV-001')
        self.assertEqual(first.document_revision, 'B')
        self.assertEqual(first.source_references[0]['file_id'], self.file.id)
        self.assertEqual(basis.deliverables.get(document_number='CIV-003').status, 'excluded')
        self.assertEqual(basis.deliverables.get(document_number='MEC-001').status, 'excluded')
        self.assertEqual(basis.deliverables.get(canonical_name='Planner added report').status, 'confirmed')
        self.assertEqual(basis.deliverables.get(canonical_name='HAZOP').source_fact_ids, [self.hazop.id])
        self.assertEqual(basis.deliverables.get(canonical_name='ENVID').status, 'excluded')
        self.assertEqual(list(self.run.facts.values_list('id', 'value', 'status')), raw_facts)
        self.assertTrue(basis.readiness['ready'])
        self.assertEqual(basis.status, 'ready')
        self.assertFalse(self.project.generation_plans.exists())
        self.assertFalse(self.project.generations.exists())

    def test_approved_basis_propagates_confirmed_hse_and_exclusions_to_wbs(self):
        self.confirm()
        basis = self.approved_basis()
        intelligence = apply_approved_basis(self.project, {'hse_studies': ['ENVID', 'HAZOP']})
        self.assertEqual(intelligence['hse_studies'], ['HAZOP'])
        self.assertFalse(intelligence['disciplines']['mechanical']['in_scope'])
        wbs = build_wbs(self.project, intelligence)
        self.assertEqual({item['discipline'] for item in wbs if item.get('discipline')}, {'civil', 'hse'})
        self.assertNotIn('Inspection report', {item['name'] for item in wbs})
        self.assertEqual(intelligence['schedule_basis_id'], basis.id)

    @patch('apps.planning_intelligence.services.workable_plan.generate_schedule')
    def test_builder_creates_new_basis_for_new_confirmation_and_reuses_matching_draft(self, generate):
        old = build_schedule_basis(self.run)
        old.status = 'approved'
        old.save(update_fields=['status'])
        preview = deepcopy(self.preview)
        preview['detected_effective_date_text'] = None
        self.confirm(preview)

        result = build_workable_plan(self.project, self.user, {}, lambda *args: None)
        self.assertEqual(result['state'], 'needs_decisions')
        self.assertEqual(result['preview_confirmation_at'], self.run.summary['preview_confirmation']['confirmed_at'])
        self.assertNotEqual(result['basis_id'], old.id)
        self.assertEqual(self.project.schedule_bases.count(), 2)
        old.refresh_from_db()
        self.assertEqual(old.status, 'approved')
        repeated = build_workable_plan(self.project, self.user, {}, lambda *args: None)
        self.assertEqual(repeated['basis_id'], result['basis_id'])
        self.assertEqual(self.project.schedule_bases.count(), 2)
        self.confirm(preview)
        revised = build_workable_plan(self.project, self.user, {}, lambda *args: None)
        self.assertNotEqual(revised['basis_id'], result['basis_id'])
        self.assertNotEqual(revised['preview_confirmation_at'], result['preview_confirmation_at'])
        generate.assert_not_called()

    def test_changed_source_blocks_old_confirmation_without_creating_basis(self):
        self.confirm()
        self.file.parse_status = 'processing'
        self.file.save(update_fields=['parse_status', 'updated_at'])
        with self.assertRaisesRegex(ValueError, 'Confirm and save'):
            build_schedule_basis(self.run)
        with self.assertRaisesRegex(ValueError, 'Confirm and save'):
            build_workable_plan(self.project, self.user, {}, lambda *args: None)
        self.assertFalse(self.project.schedule_bases.exists())

    def test_generation_cannot_reuse_prior_approved_scope_after_new_confirmation(self):
        self.confirm()
        basis = self.approved_basis()
        preview = deepcopy(self.preview)
        preview['hse_studies'] = []
        self.confirm(preview)
        with self.assertRaisesRegex(ValueError, 'Build the schedule basis'):
            apply_approved_basis(self.project, {})
        basis.refresh_from_db()
        self.assertEqual(basis.status, 'approved')
        self.assertEqual(basis.deliverables.get(canonical_name='HAZOP').status, 'confirmed')
