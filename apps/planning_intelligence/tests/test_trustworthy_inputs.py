import datetime

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project

from .test_business_approval_gates import grant_test_approval
from ..models import (
    DocumentAuthorityRule, DocumentIntelligenceRun, IntelligenceConflict,
    IntelligenceFact, PlanningFile, PlanningProject,
)
from ..services.document_intelligence import run_document_intelligence
from ..services.schedule_basis import (
    apply_approved_basis, approve_schedule_basis, build_schedule_basis,
    refresh_basis_readiness,
)


class TrustworthyInputsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='basis-planner', email='basis-planner@example.test', password='test',
        )
        grant_test_approval((self.user,))
        enterprise = Project.objects.create(code='BASIS-TEST', name='Evidence Project', owner=self.user)
        self.project = PlanningProject.objects.create(
            name='Evidence Project', effective_date=datetime.date(2026, 1, 1),
            planned_end_date=datetime.date(2026, 5, 1), duration_months=4,
            created_by=self.user, enterprise_project=enterprise,
        )
        DocumentAuthorityRule.objects.update_or_create(
            information_type='deliverables', document_category='mdr', defaults={'priority': 100},
        )

    def _file(self, category, name, text):
        return PlanningFile.objects.create(
            project=self.project, category=category,
            file=SimpleUploadedFile(name, text.encode()), original_filename=name,
            content_type='text/plain', parse_status='done', extracted_text=text,
            confidence_score=.95, uploaded_by=self.user,
        )

    def test_register_rows_preserve_identity_and_are_deduplicated(self):
        mdr = self._file('mdr', 'mdr.txt', '\n'.join([
            'MASTER DELIVERABLE REGISTER',
            '1 CIVIL & STRUCTURAL PJ-ABC-CIV-0001 VISUAL INSPECTION REPORT MUBARRAZ ISLAND NEW 1 A',
            '2 CIVIL & STRUCTURAL PJ-ABC-CIV-0002 NDT FINDINGS REPORT MUBARRAZ ISLAND NEW 1 B',
        ]))
        run, _ = run_document_intelligence(self.project, user=self.user, files=[mdr])
        basis = build_schedule_basis(run)
        visual = basis.deliverables.get(document_number='PJ-ABC-CIV-0001')
        self.assertEqual(visual.canonical_name, 'VISUAL INSPECTION REPORT MUBARRAZ ISLAND')
        self.assertEqual(visual.original_title, 'VISUAL INSPECTION REPORT MUBARRAZ ISLAND')
        self.assertEqual(visual.document_revision, 'A')
        self.assertTrue(visual.source_references[0]['locator']['line'])
        self.assertEqual(basis.deliverables.filter(document_number='PJ-ABC-CIV-0001').count(), 1)
        ndt = basis.deliverables.get(document_number='PJ-ABC-CIV-0002')
        self.assertEqual(ndt.canonical_name, 'NDT FINDINGS REPORT MUBARRAZ ISLAND')
        self.assertEqual(ndt.original_title, 'NDT FINDINGS REPORT MUBARRAZ ISLAND')
        self.assertEqual(ndt.document_revision, 'B')

    def test_shared_title_words_are_not_mistaken_for_an_area_column(self):
        mdr = self._file('mdr', 'titles.txt', '\n'.join([
            'MASTER DELIVERABLE REGISTER',
            '1 CIVIL & STRUCTURAL PJ-ABC-CIV-0001 VISUAL INSPECTION REPORT NEW 1 A',
            '2 CIVIL & STRUCTURAL PJ-ABC-CIV-0002 MECHANICAL INSPECTION REPORT NEW 1 B',
            '3 CIVIL & STRUCTURAL PJ-ABC-CIV-0003 DESIGN BASIS FOR COMPANY REVIEW NEW 1 A',
            '4 CIVIL & STRUCTURAL PJ-ABC-CIV-0004 MATERIAL SELECTION FOR COMPANY REVIEW NEW 1 A',
        ]))
        run, _ = run_document_intelligence(self.project, user=self.user, files=[mdr])
        basis = build_schedule_basis(run)
        expected = {
            'PJ-ABC-CIV-0001': 'VISUAL INSPECTION REPORT',
            'PJ-ABC-CIV-0002': 'MECHANICAL INSPECTION REPORT',
            'PJ-ABC-CIV-0003': 'DESIGN BASIS FOR COMPANY REVIEW',
            'PJ-ABC-CIV-0004': 'MATERIAL SELECTION FOR COMPANY REVIEW',
        }
        for number, original in expected.items():
            self.assertEqual(basis.deliverables.get(document_number=number).original_title, original)

    def test_repeated_register_rows_remain_distinct_until_identity_review(self):
        mdr = self._file('mdr', 'duplicates.txt', '\n'.join([
            'MASTER DELIVERABLE REGISTER',
            '1 CIVIL & STRUCTURAL PJ-ABC-CIV-0001 VISUAL INSPECTION REPORT WEST ISLAND NEW 1 A',
            '1 CIVIL & STRUCTURAL PJ-ABC-CIV-0001 VISUAL INSPECTION REPORT WEST ISLAND NEW 1 A',
            '2 CIVIL & STRUCTURAL PJ-ABC-CIV-0002 VISUAL INSPECTION REPORT WEST ISLAND NEW 1 B',
        ]))
        run, _ = run_document_intelligence(self.project, user=self.user, files=[mdr])
        basis = build_schedule_basis(run)
        self.assertEqual(basis.deliverables.count(), 3)
        self.assertEqual(len(set(basis.deliverables.values_list('canonical_key', flat=True))), 3)
        self.assertEqual(set(basis.deliverables.values_list('document_number', flat=True)), {'PJ-ABC-CIV-0001', 'PJ-ABC-CIV-0002'})
        self.assertEqual(set(basis.deliverables.values_list('canonical_name', flat=True)), {'VISUAL INSPECTION REPORT WEST ISLAND'})
        self.assertEqual(set(basis.deliverables.values_list('original_title', flat=True)), {'VISUAL INSPECTION REPORT WEST ISLAND'})

    def test_conflicts_and_unreviewed_deliverables_block_approval(self):
        run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', engine_version='2.0',
            started_at=timezone.now(), finished_at=timezone.now(), requested_by=self.user,
        )
        first = IntelligenceFact.objects.create(
            run=run, fact_type='effective_date', key='effective_date', value='2026-01-01',
            normalized_value='2026-01-01', confidence=.9,
        )
        second = IntelligenceFact.objects.create(
            run=run, fact_type='effective_date', key='effective_date', value='2026-02-01',
            normalized_value='2026-02-01', confidence=.9, status='conflicted',
        )
        first.status = 'conflicted'
        first.save(update_fields=['status'])
        IntelligenceConflict.objects.create(
            run=run, key='effective_date:effective_date', fact_ids=[first.id, second.id],
            description='Dates disagree.',
        )
        IntelligenceFact.objects.create(
            run=run, fact_type='deliverable', key='civil:test', extraction_method='manual',
            value={'discipline': 'civil', 'name': 'Test Report'},
            normalized_value='test report', confidence=.9,
        )
        basis = build_schedule_basis(run)
        self.assertFalse(basis.readiness['ready'])
        with self.assertRaises(ValueError):
            approve_schedule_basis(basis, self.user)

        conflict = run.conflicts.get()
        conflict.status = 'resolved'
        conflict.save(update_fields=['status'])
        deliverable = basis.deliverables.get()
        deliverable.status = 'confirmed'
        deliverable.save(update_fields=['status'])
        refresh_basis_readiness(basis)
        basis.refresh_from_db()
        self.assertFalse(basis.readiness['ready'])
        # Closing a conflict label alone does not select an accepted value.
        first.status, first.reviewed_by, first.reviewed_at = 'confirmed', self.user, timezone.now()
        first.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
        second.status = 'rejected'
        second.save(update_fields=['status'])
        run.facts.filter(fact_type='deliverable').update(status='confirmed', reviewed_by=self.user, reviewed_at=timezone.now())
        reviewed_basis = build_schedule_basis(run)
        self.assertTrue(reviewed_basis.readiness['ready'])
        self.assertEqual(reviewed_basis.effective_date, datetime.date(2026, 1, 1))

    def test_approved_basis_is_the_generation_scope(self):
        run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', engine_version='2.0',
            started_at=timezone.now(), finished_at=timezone.now(), requested_by=self.user,
        )
        IntelligenceFact.objects.create(
            run=run, fact_type='deliverable', key='civil:confirmed', extraction_method='manual',
            value={'discipline': 'civil', 'name': 'Confirmed Report'},
            normalized_value='confirmed report', confidence=.9, status='confirmed',
        )
        basis = build_schedule_basis(run)
        approve_schedule_basis(basis, self.user)
        intelligence = apply_approved_basis(self.project, {
            'disciplines': {'mechanical': {'in_scope': True, 'deliverables': ['Generic Default']}},
        })
        self.assertEqual(intelligence['schedule_basis_id'], basis.id)
        self.assertEqual(intelligence['disciplines']['civil']['deliverables'], ['Confirmed Report'])
        self.assertNotIn('mechanical', intelligence['disciplines'])
