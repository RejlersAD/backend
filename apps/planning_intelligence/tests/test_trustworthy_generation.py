import datetime
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project

from .test_business_approval_gates import grant_test_approval
from ..models import BasisDeliverable, DocumentIntelligenceRun, GenerationDependency, PlanningFile, PlanningProject, ScheduleBasis
from ..services.generation_plan import (
    apply_approved_generation_plan, approve_generation_plan, build_generation_plan,
    classify_deliverable, refresh_generation_plan_readiness,
)
from ..services.schedule_basis import apply_approved_basis


class TrustworthyGenerationTests(TestCase):
    def setUp(self):
        # The isolated test settings sync model tables without data migrations.
        # Load the same workflow definitions supplied to a migrated installation.
        import_module('apps.planning_intelligence.migrations.0021_trustworthy_generation').seed_workflow_families(apps, None)
        self.user = get_user_model().objects.create_user(
            username='generation-planner', email='generation-planner@example.test', password='test',
        )
        grant_test_approval((self.user,))
        enterprise = Project.objects.create(code='GENERATION-TEST', name='Generation Project', owner=self.user)
        self.project = PlanningProject.objects.create(
            name='Generation Project', effective_date=datetime.date(2026, 1, 1),
            planned_end_date=datetime.date(2026, 5, 1), duration_months=4, created_by=self.user,
            enterprise_project=enterprise,
        )
        source = PlanningFile.objects.create(
            project=self.project, category='sow',
            file=SimpleUploadedFile('scope.txt', b'2 months offshore site activities\n2 months project management, engineering and study'),
            original_filename='scope.txt', content_type='text/plain', parse_status='done',
            extracted_text='2 months offshore site activities\n2 months project management, engineering and study',
            uploaded_by=self.user,
        )
        run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', engine_version='test', source_file_ids=[source.id],
            started_at=timezone.now(), finished_at=timezone.now(), requested_by=self.user,
        )
        self.basis = ScheduleBasis.objects.create(
            project=self.project, source_run=run, version=1, status='approved',
            project_name=self.project.name, effective_date=self.project.effective_date,
            contractual_finish=self.project.planned_end_date, duration_months=4,
            approved_by=self.user, approved_at=timezone.now(), readiness={'ready': True},
        )

    def _deliverable(self, name, number, discipline='civil'):
        return BasisDeliverable.objects.create(
            basis=self.basis, discipline=discipline, canonical_key=number.lower(),
            canonical_name=name, original_title=name, document_number=number,
            status='confirmed', confidence=.95,
            source_references=[{'file_id': self.basis.source_run.source_file_ids[0], 'filename': 'scope.txt', 'locator': {'line': 1}}],
        )

    def test_titles_and_number_ranges_do_not_establish_recurrence_workflows_or_scenarios(self):
        weekly = self._deliverable('Weekly Progress Reports', 'PJ-GEN-0001~0020', 'general')
        repair = self._deliverable('Structural Repair Works Drawing', 'PJ-CIV-0001')
        scrap = self._deliverable('Comprehensive Study for Scrap and Build Option', 'PJ-CIV-0002')
        for deliverable in (weekly, repair, scrap):
            classified = classify_deliverable(deliverable)
            self.assertEqual(classified['recurrence_count'], 1)
            self.assertEqual(classified['recurrence'], 'none')
            self.assertEqual(classified['workflow_family'], 'not_specified')
            self.assertEqual(classified['scenario_code'], 'common')
            self.assertEqual(classified['technical_sequence'], 0)

    def test_plan_keeps_confirmed_scope_without_inferred_phases_dependencies_or_gates(self):
        self._deliverable('Visual Inspection Report', 'PJ-CIV-0001')
        self._deliverable('NDT Findings Report', 'PJ-CIV-0002')
        self._deliverable('Structural Adequacy Assessment', 'PJ-CIV-0003')
        self._deliverable('Structural Repair Works Drawing', 'PJ-CIV-0004')
        self._deliverable('Comprehensive Study for Scrap and Build Option', 'PJ-CIV-0005')
        self._deliverable('Weekly Progress Reports', 'PJ-GEN-0001~0003', 'general')
        self._deliverable('Final Dossier', 'PJ-GEN-0006', 'general')
        plan = build_generation_plan(self.basis)
        self.assertEqual(plan.phases.count(), 0)
        self.assertEqual(plan.dependencies.count(), 0)
        self.assertEqual(plan.decision_gates.count(), 0)
        self.assertEqual(plan.deliverables.count(), 7)
        self.assertTrue(plan.readiness['ready'])
        approve_generation_plan(plan, self.user)
        intelligence = apply_approved_generation_plan(self.project, apply_approved_basis(self.project, {}))
        self.assertEqual(intelligence['generation_plan_id'], plan.pk)
        self.assertNotIn('generation_plan_dependencies', intelligence)
        self.assertNotIn('generation_plan_phases', intelligence)

    def test_recurring_report_name_retains_source_identity_without_inventing_occurrences(self):
        weekly = self._deliverable('Weekly Progress Reports', 'PJ-GEN-0001~0003', 'general')
        plan = build_generation_plan(self.basis)
        entries = list(plan.deliverables.select_related('basis_deliverable'))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].basis_deliverable_id, weekly.pk)
        self.assertEqual(entries[0].basis_deliverable.source_references, weekly.source_references)
        self.assertEqual(entries[0].recurrence_count, 1)
        self.assertEqual(entries[0].workflow_family, 'not_specified')
        self.assertEqual(plan.dependencies.count(), 0)

    def test_confirmed_scope_is_not_removed_by_a_scenario_inferred_from_its_title(self):
        repair = self._deliverable('Structural Repair Works Drawing', 'PJ-CIV-0004')
        scrap = self._deliverable('Comprehensive Study for Scrap and Build Option', 'PJ-CIV-0005')
        plan = build_generation_plan(self.basis)
        self.assertEqual(set(plan.deliverables.values_list('basis_deliverable_id', flat=True)), {repair.id, scrap.id})
        self.assertEqual(plan.readiness['available_scenarios'], [])
        self.assertTrue(plan.readiness['ready'])

    def test_explicit_confirmed_dependency_cycles_still_block_approval(self):
        self._deliverable('First documented package', 'DOC-1')
        self._deliverable('Second documented package', 'DOC-2')
        plan = build_generation_plan(self.basis)
        first, second = list(plan.deliverables.order_by('pk'))
        for predecessor, successor in ((first, second), (second, first)):
            GenerationDependency.objects.create(
                plan=plan, predecessor=predecessor, successor=successor,
                relationship_type='FS', lag_days=0, status='confirmed', source_type='planner',
                rationale='Explicit test cycle; must not be automatically accepted.')
        readiness = refresh_generation_plan_readiness(plan)
        self.assertFalse(readiness['ready'])
        self.assertTrue(readiness['has_dependency_cycle'])
        with self.assertRaisesMessage(ValueError, 'cycle'):
            approve_generation_plan(plan, self.user)
