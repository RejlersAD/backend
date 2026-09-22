"""Full preview confirmation is durable, scoped and tied to reviewed inputs."""
from copy import deepcopy
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions

from ..models import DocumentIntelligenceRun, IntelligenceConflict, IntelligenceFact, PlanningAuditEvent
from ..services.preview_confirmation import current_confirmed_preview, source_fingerprint
from .test_document_intelligence import DocumentIntelligenceFixture


class PreviewConfirmationTests(DocumentIntelligenceFixture):
    def setUp(self):
        super().setUp()
        organization = Organization.objects.create(name='Preview tests', code='preview-test')
        self.role = Role.objects.create(name='Preview planner', code='preview-planner', level=4)
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'update', 'create'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        for user in (self.owner, self.outsider):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            # Isolate this role from grants installed by migration seed data.
            UserRole.objects.filter(user_profile=profile).delete()
            UserRole.objects.create(user_profile=profile, role=self.role)
        self.file = self.source('scope.txt', 'sow', 'Electrical drawings and a HAZOP study.')
        self.raw = {
            'detected_project_name': 'Intelligence Project',
            'detected_effective_date_text': '2026-10-05', 'detected_duration_months': 3,
            'disciplines': {'electrical': {
                'in_scope': True, 'deliverables': ['Single line diagram', 'Cable list'],
                'mentioned_in_source': ['Single line diagram'], 'source_references': [{'line': 1}],
            }},
            'hse_studies': ['HAZOP'], 'available_hse_studies': ['HAZOP', 'ENVID'],
        }
        self.run = DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', source_file_ids=[self.file.pk],
            started_at=timezone.now(), finished_at=timezone.now(), fact_count=1,
            summary={'base_intelligence': deepcopy(self.raw), 'source_fingerprint': source_fingerprint(self.project)},
        )
        self.fact = IntelligenceFact.objects.create(
            run=self.run, source_file=self.file, fact_type='requirement', key='requirement:electrical',
            value='Electrical drawings', source_excerpt='Electrical drawings and a HAZOP study.',
            source_locator={'line': 1}, status='detected',
        )
        self.selection = {
            'detected_project_name': 'Reviewed Project', 'detected_effective_date_text': '2026-11-01',
            'detected_duration_months': 4,
            'disciplines': {'electrical': {
                'in_scope': True, 'excluded_deliverables': ['Cable list'],
                'deliverables': ['Single line diagram', 'Cable list', 'Additional drawing'],
            }},
            'hse_studies': ['ENVID'],
        }
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.run_url = f'/api/v1/planning-intelligence/intelligence-runs/{self.run.pk}/'
        self.url = self.run_url + 'confirm-preview/'

    def confirm(self):
        return self.client.post(self.url, {'preview': self.selection}, format='json')

    def test_confirm_restores_edits_and_preserves_raw_evidence(self):
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['preview_confirmation']['is_current'])
        self.assertEqual(response.data['preview_confirmation']['confirmed_by'], self.owner.pk)
        self.run.refresh_from_db()
        self.fact.refresh_from_db()
        self.assertEqual(self.run.summary['base_intelligence'], self.raw)
        self.assertEqual(self.fact.status, 'confirmed')
        self.assertEqual(self.fact.reviewed_by, self.owner)
        self.assertEqual(self.fact.value, 'Electrical drawings')
        self.assertEqual(self.fact.source_locator, {'line': 1})
        self.assertEqual(current_confirmed_preview(self.run), self.selection)
        restored = self.client.get(self.run_url).data
        self.assertEqual(restored['intelligence']['detected_project_name'], 'Reviewed Project')
        self.assertEqual(restored['intelligence']['hse_studies'], ['ENVID'])
        self.assertEqual(restored['intelligence']['disciplines']['electrical']['excluded_deliverables'], ['Cable list'])
        self.assertEqual(restored['intelligence']['disciplines']['electrical']['source_references'], [{'line': 1}])
        audit = PlanningAuditEvent.objects.get(action='intelligence.preview_confirmed')
        self.assertEqual(audit.actor_id, self.owner.pk)
        self.assertEqual(audit.after['preview'], self.selection)
        self.assertFalse(self.project.schedule_bases.exists())
        self.assertFalse(self.project.generations.exists())

    def test_omitted_catalogue_retains_original_deliverables(self):
        self.selection['disciplines']['electrical'].pop('deliverables')
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['intelligence']['disciplines']['electrical']['deliverables'],
                         self.raw['disciplines']['electrical']['deliverables'])

    def test_reconfirm_records_new_choices_without_overwriting_evidence(self):
        self.assertEqual(self.confirm().status_code, 200)
        self.selection['disciplines']['electrical']['in_scope'] = False
        self.selection['hse_studies'] = []
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['intelligence']['disciplines']['electrical']['in_scope'])
        self.assertEqual(PlanningAuditEvent.objects.filter(action='intelligence.preview_confirmed').count(), 2)
        self.run.refresh_from_db()
        self.assertEqual(self.run.summary['base_intelligence'], self.raw)

    def test_source_upload_or_reparse_invalidates_confirmation(self):
        self.assertEqual(self.confirm().status_code, 200)
        self.file.extracted_text = 'Updated file contents'
        self.file.save()
        response = self.confirm()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'intelligence_sources_changed')
        restored = self.client.get(self.run_url).data
        self.assertFalse(restored['preview_confirmation']['is_current'])
        self.assertIsNone(restored['intelligence']['detected_project_name'])
        self.run.refresh_from_db()
        self.assertEqual(self.run.summary['base_intelligence'], self.raw)
        self.assertFalse(self.run.facts.filter(fact_type='project_name').exists())

    def test_pending_new_upload_prevents_confirmation(self):
        pending = self.source('mdr.csv', 'mdr', 'Document,Discipline')
        pending.parse_status = 'pending'
        pending.save()
        response = self.confirm()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'intelligence_sources_changed')
        self.assertFalse(PlanningAuditEvent.objects.filter(action='intelligence.preview_confirmed').exists())

    def test_stale_confirmation_returns_conflict_when_building_basis(self):
        self.assertEqual(self.confirm().status_code, 200)
        self.file.save()
        response = self.client.post(self.run_url + 'build-schedule-basis/', {}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('error', response.data)
        self.assertFalse(self.project.schedule_bases.exists())

    def test_project_edit_or_newer_run_prevents_confirmation(self):
        self.project.phase = 'FEED'
        self.project.save()
        self.assertEqual(self.confirm().data['code'], 'intelligence_sources_changed')
        DocumentIntelligenceRun.objects.create(
            project=self.project, status='succeeded', started_at=timezone.now(),
        )
        response = self.confirm()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'intelligence_run_not_current')

    def test_open_and_ignored_conflicts_prevent_bulk_confirmation(self):
        conflict = IntelligenceConflict.objects.create(
            run=self.run, key='effective_date:effective_date', description='Dates disagree',
        )
        for review_status in ('open', 'ignored'):
            conflict.status = review_status
            conflict.save()
            response = self.confirm()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['code'], 'intelligence_conflicts_unresolved')
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.status, 'detected')

    def test_evidence_review_change_invalidates_saved_confirmation(self):
        self.assertEqual(self.confirm().status_code, 200)
        self.fact.status = 'rejected'
        self.fact.save()
        self.assertFalse(self.client.get(self.run_url).data['preview_confirmation']['is_current'])
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.status, 'rejected')

    def test_invalid_scope_and_duration_do_not_save(self):
        original = deepcopy(self.selection)
        mutations = [
            {'detected_duration_months': -1},
            {'hse_studies': ['Not in preview']},
            {'disciplines': {'unknown': {'in_scope': True, 'excluded_deliverables': []}}},
            {'disciplines': {'electrical': {'in_scope': True, 'excluded_deliverables': ['Unknown']}}},
        ]
        for changes in mutations:
            self.selection = {**deepcopy(original), **changes}
            self.assertEqual(self.confirm().status_code, 400)
        self.assertFalse(PlanningAuditEvent.objects.filter(action='intelligence.preview_confirmed').exists())
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.status, 'detected')

    def test_unauthorized_project_is_not_exposed(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.confirm().status_code, 404)

    def test_read_module_permission_does_not_allow_preview_confirmation(self):
        RolePermission.objects.filter(role=self.role, permission__action='update').delete()
        self.assertEqual(self.client.get(self.run_url).status_code, 200)
        self.assertEqual(self.confirm().status_code, 403)

    def test_confirmation_and_fact_reviews_roll_back_when_audit_save_fails(self):
        with patch('apps.planning_intelligence.intelligence_views.record_event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.confirm()
        self.run.refresh_from_db()
        self.fact.refresh_from_db()
        self.assertNotIn('preview_confirmation', self.run.summary)
        self.assertEqual(self.fact.status, 'detected')

    def test_historical_unchanged_run_can_be_confirmed(self):
        self.run.summary.pop('source_fingerprint')
        self.run.save()
        self.assertEqual(self.confirm().status_code, 200)
