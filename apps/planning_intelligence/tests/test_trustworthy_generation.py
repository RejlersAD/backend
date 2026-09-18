import datetime
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project

from .test_business_approval_gates import grant_test_approval
from ..models import BasisDeliverable, DocumentIntelligenceRun, PlanningFile, PlanningProject, ScheduleBasis
from ..services.activity_generator import build_activities
from ..services.generation_plan import (
    apply_approved_generation_plan, approve_generation_plan, build_generation_plan,
    classify_deliverable, refresh_generation_plan_readiness,
)
from ..services.eddr_generator import build_eddr
from ..services.schedule_basis import apply_approved_basis
from ..services.validation_engine import validate
from ..services.wbs_generator import build_wbs


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

    def test_classification_preserves_recurrence_and_conditional_scenarios(self):
        weekly = self._deliverable('Weekly Progress Reports', 'PJ-GEN-0001~0020', 'general')
        repair = self._deliverable('Structural Repair Works Drawing', 'PJ-CIV-0001')
        scrap = self._deliverable('Comprehensive Study for Scrap and Build Option', 'PJ-CIV-0002')
        self.assertEqual(classify_deliverable(weekly)['recurrence_count'], 20)
        self.assertEqual(classify_deliverable(weekly)['workflow_family'], 'recurring_report')
        self.assertEqual(classify_deliverable(repair)['scenario_code'], 'continue_use')
        self.assertEqual(classify_deliverable(scrap)['scenario_code'], 'scrap_build')

    def test_approved_plan_generates_only_selected_scope_without_generic_tails(self):
        self._deliverable('Visual Inspection Report', 'PJ-CIV-0001')
        self._deliverable('NDT Findings Report', 'PJ-CIV-0002')
        self._deliverable('Structural Adequacy Assessment', 'PJ-CIV-0003')
        self._deliverable('Structural Repair Works Drawing', 'PJ-CIV-0004')
        self._deliverable('Comprehensive Study for Scrap and Build Option', 'PJ-CIV-0005')
        self._deliverable('Weekly Progress Reports', 'PJ-GEN-0001~0003', 'general')
        self._deliverable('Final Dossier', 'PJ-GEN-0006', 'general')
        plan = build_generation_plan(self.basis)
        self.assertEqual(dict(plan.phases.values_list('code', 'duration_months'))['fieldwork'], 2)
        self.assertFalse(plan.readiness['ready'])
        plan.dependencies.update(status='confirmed')
        plan.selected_scenario = 'continue_use'
        plan.save(update_fields=['selected_scenario'])
        refresh_generation_plan_readiness(plan)
        approve_generation_plan(plan, self.user)

        intelligence = apply_approved_generation_plan(self.project, apply_approved_basis(self.project, {}))
        generated = build_activities(self.project, build_wbs(self.project, intelligence), intelligence)
        names = [item['name'] for item in generated['activities']]
        self.assertFalse(any('Project Definition Report' in name for name in names))
        self.assertFalse(any('EPC Tender Package' in name for name in names))
        self.assertFalse(any('Scrap and Build Option' in name for name in names))
        self.assertEqual(sum('Weekly Progress Reports #' in name and 'Submit' in name for name in names), 3)
        self.assertTrue(any(item.get('source') == 'generation_plan_dependency' for item in generated['logic_matrix']))

    def test_recurring_reports_keep_each_submission_and_source_identity(self):
        weekly = self._deliverable('Weekly Progress Reports', 'PJ-GEN-0001~0003', 'general')
        plan = build_generation_plan(self.basis)
        approve_generation_plan(plan, self.user)
        intelligence = apply_approved_generation_plan(self.project, apply_approved_basis(self.project, {}))
        generated = build_activities(self.project, build_wbs(self.project, intelligence), intelligence)
        reports = [row for row in generated['activities'] if row.get('basis_deliverable_id') == weekly.pk]

        self.assertEqual(len(reports), 9)  # Period close, preparation and submission for each period.
        self.assertEqual({row['recurrence_occurrence'] for row in reports}, {1, 2, 3})
        for occurrence in (1, 2, 3):
            group = [row for row in reports if row['recurrence_occurrence'] == occurrence]
            self.assertEqual(len([row for row in group if row['name'].endswith(' - Submit')]), 1)
            self.assertTrue(all(row['document_number'] == weekly.document_number for row in group))
            self.assertTrue(all(row['source_references'] == weekly.source_references for row in group))
            self.assertTrue(all(row['workflow_template_code'] == 'RECURRING_REPORT' for row in group))
            self.assertTrue(all(row['workflow_template_version'] == 1 for row in group))
            recurrence_links = [link for row in group for link in row['predecessors'] if link.get('source') == 'recurrence_rule']
            self.assertEqual(len(recurrence_links), 1)
            self.assertEqual(recurrence_links[0]['lag_days'], (occurrence - 1) * 5)

    def test_coverage_validation_does_not_require_unselected_scenario(self):
        repair = self._deliverable('Structural Repair Works Drawing', 'PJ-CIV-0004')
        scrap = self._deliverable('Comprehensive Study for Scrap and Build Option', 'PJ-CIV-0005')
        plan = build_generation_plan(self.basis)
        plan.dependencies.update(status='confirmed')
        plan.selected_scenario = 'continue_use'
        plan.save(update_fields=['selected_scenario'])
        refresh_generation_plan_readiness(plan)
        approve_generation_plan(plan, self.user)
        intelligence = apply_approved_generation_plan(self.project, apply_approved_basis(self.project, {}))
        wbs = build_wbs(self.project, intelligence)
        generated = build_activities(self.project, wbs, intelligence)
        issues = validate(
            self.project, wbs, generated['activities'], build_eddr(generated['activities']), intelligence,
        )
        coverage = next(issue for issue in issues if issue['rule'] == 'deliverable_coverage')

        self.assertEqual(coverage['severity'], 'pass')
        scheduled_ids = {item.get('basis_deliverable_id') for item in generated['activities']}
        self.assertIn(repair.id, scheduled_ids)
        self.assertNotIn(scrap.id, scheduled_ids)
