"""Full preview confirmation is durable, scoped and tied to reviewed inputs."""
from copy import deepcopy
from unittest.mock import patch

from django.test import override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from ..intelligence_views import DocumentIntelligenceRunViewSet, IntelligenceConflictViewSet, IntelligenceFactViewSet
from ..models import DocumentIntelligenceRun, IntelligenceConflict, IntelligenceFact, PlanningAuditEvent
from ..services.preview_confirmation import current_confirmed_preview, source_fingerprint
from ..services.document_intelligence import run_document_intelligence
from .test_document_intelligence import DocumentIntelligenceFixture


router = DefaultRouter()
router.register('intelligence-runs', DocumentIntelligenceRunViewSet, basename='preview-retention-run')
router.register('intelligence-facts', IntelligenceFactViewSet, basename='preview-retention-fact')
router.register('intelligence-conflicts', IntelligenceConflictViewSet, basename='preview-retention-conflict')
urlpatterns = [path('api/v1/planning-intelligence/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
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

    def grant_fact_rejection(self):
        # The existing route policy treats an explicit rejected status as a
        # decision. Review-retention fixtures must carry that capability.
        for permission in Permission.objects.filter(module__code='planning_package', action='approve', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

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

    def analysed_preview(self, *, text=None):
        self.file.extracted_text = text or 'The Contractor shall prepare the coordination drawings.'
        self.file.save()
        self.run, intelligence = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.run_url = f'/api/v1/planning-intelligence/intelligence-runs/{self.run.pk}/'
        self.url = self.run_url + 'confirm-preview/'
        self.selection = {key: intelligence[key] for key in (
            'detected_project_name', 'detected_effective_date_text', 'detected_duration_months',
            'disciplines', 'hse_studies',
        )}
        return self.run

    def test_reanalysis_preserves_exact_reviews_and_confirmed_preview_after_reload(self):
        self.grant_fact_rejection()
        previous = self.analysed_preview()
        requirement = previous.facts.get(fact_type='requirement')
        response = self.client.post(
            f'/api/v1/planning-intelligence/intelligence-facts/{requirement.pk}/review/',
            {'status': 'rejected'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.selection['detected_project_name'] = 'Planner reviewed title'
        self.assertEqual(self.confirm().status_code, 200)
        previous.refresh_from_db()
        original_summary = deepcopy(previous.summary)
        old_reviews = {fact.key: (fact.status, fact.reviewed_by_id, fact.reviewed_at)
                       for fact in previous.facts.all()}

        current, intelligence = run_document_intelligence(self.project, user=self.owner, allow_ai=False)

        self.assertNotEqual(current.pk, previous.pk)
        self.assertEqual(intelligence['detected_project_name'], 'Planner reviewed title')
        self.assertEqual({fact.key: (fact.status, fact.reviewed_by_id, fact.reviewed_at)
                          for fact in current.facts.all()}, old_reviews)
        restored = self.client.get(f'/api/v1/planning-intelligence/intelligence-runs/{current.pk}/').data
        self.assertTrue(restored['preview_confirmation']['is_current'])
        self.assertEqual(restored['preview_confirmation']['confirmed_at'],
                         original_summary['preview_confirmation']['confirmed_at'])
        self.assertEqual(current.summary['preview_confirmation']['retained_from_run_id'], previous.pk)
        audit = PlanningAuditEvent.objects.get(action='intelligence.reviews_retained', entity_id=str(current.pk))
        self.assertTrue(audit.after['preview_retained'])
        self.assertEqual(audit.after['previous_run_id'], previous.pk)
        previous.refresh_from_db()
        self.assertEqual(previous.summary, original_summary)
        self.assertFalse(self.project.schedule_bases.exists())

    def test_changed_source_or_project_inputs_require_fresh_confirmation(self):
        for change in ('source', 'project'):
            with self.subTest(change=change):
                self.analysed_preview()
                self.assertEqual(self.confirm().status_code, 200)
                if change == 'source':
                    self.file.extracted_text += '\nThe Contractor shall issue a separate report.'
                    self.file.save()
                else:
                    self.project.phase = 'Changed phase'
                    self.project.save()
                current, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
                self.assertNotIn('preview_confirmation', current.summary)
                self.assertFalse(current.facts.filter(status='confirmed').exists())
                self.assertFalse(current.facts.filter(reviewed_at__isnull=False).exists())

    def test_review_changed_after_confirmation_is_retained_without_stale_preview(self):
        self.grant_fact_rejection()
        previous = self.analysed_preview()
        self.assertEqual(self.confirm().status_code, 200)
        requirement = previous.facts.get(fact_type='requirement')
        response = self.client.post(
            f'/api/v1/planning-intelligence/intelligence-facts/{requirement.pk}/review/',
            {'status': 'rejected'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)

        current, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)

        self.assertEqual(current.facts.get(fact_type='requirement').status, 'rejected')
        self.assertNotIn('preview_confirmation', current.summary)

    def test_source_rejection_retains_existing_decision_permission_guard(self):
        response = self.client.post(
            f'/api/v1/planning-intelligence/intelligence-facts/{self.fact.pk}/review/',
            {'status': 'rejected'}, format='json',
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.status, 'detected')
        self.assertIsNone(self.fact.reviewed_at)
        self.assertFalse(PlanningAuditEvent.objects.filter(action='intelligence.fact_rejected').exists())

    def test_new_assertions_keep_old_decisions_but_require_preview_confirmation(self):
        self.analysed_preview()
        self.assertEqual(self.confirm().status_code, 200)
        from ..services.document_intelligence import _extract_file_facts

        def extra_assertion(rows, source, **kwargs):
            _extract_file_facts(rows, source, **kwargs)
            rows['facts'].append(IntelligenceFact(
                run=rows['run'], source_file=source, fact_type='requirement', key='new-assertion',
                value='Coordination drawings', normalized_value='coordination drawings',
                source_excerpt='coordination drawings', source_locator={'line': 1}, extraction_method='ai',
            ))

        with patch('apps.planning_intelligence.services.document_intelligence._extract_file_facts', extra_assertion):
            current, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.assertTrue(current.facts.filter(status='confirmed').exists())
        self.assertEqual(current.facts.get(key='new-assertion').status, 'detected')
        self.assertNotIn('preview_confirmation', current.summary)

    def test_exact_conflict_resolution_survives_reanalysis(self):
        self.project.location = 'Abu Dhabi'
        self.project.save()
        previous = self.analysed_preview(text='Location: Dubai')
        conflict = previous.conflicts.get(key='location:location')
        selected = previous.facts.get(fact_type='location', source_file=self.file)
        response = self.client.post(
            f'/api/v1/planning-intelligence/intelligence-conflicts/{conflict.pk}/resolve/',
            {'action': 'select_fact', 'selected_fact_id': selected.pk}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.confirm().status_code, 200)
        conflict.refresh_from_db()

        current, intelligence = run_document_intelligence(self.project, user=self.owner, allow_ai=False)

        restored = current.conflicts.get(key=conflict.key)
        new_selected = current.facts.get(fact_type='location', source_file=self.file)
        self.assertEqual(restored.status, 'resolved')
        self.assertEqual(restored.resolved_at, conflict.resolved_at)
        self.assertEqual(restored.resolution['selected_fact_id'], new_selected.pk)
        self.assertEqual(new_selected.status, 'confirmed')
        self.assertEqual(intelligence['detected_location'], 'Dubai')
        self.assertIsNotNone(current_confirmed_preview(current))

    def test_changed_assertion_value_method_or_locator_needs_review(self):
        from ..services.document_intelligence import _extract_file_facts
        changes = {'value': 'A changed requirement', 'extraction_method': 'ai',
                   'source_locator': {'line': 9}, 'source_excerpt': 'Changed evidence excerpt'}
        for field, value in changes.items():
            with self.subTest(field=field):
                self.analysed_preview()
                self.assertEqual(self.confirm().status_code, 200)

                def changed_assertion(rows, source, **kwargs):
                    _extract_file_facts(rows, source, **kwargs)
                    fact = next(item for item in rows['facts'] if item.fact_type == 'requirement')
                    setattr(fact, field, value)

                with patch('apps.planning_intelligence.services.document_intelligence._extract_file_facts', changed_assertion):
                    current, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
                fact = current.facts.get(fact_type='requirement')
                self.assertEqual(fact.status, 'detected')
                self.assertIsNone(fact.reviewed_at)
                self.assertNotIn('preview_confirmation', current.summary)

    def test_new_conflicting_evidence_cannot_inherit_a_review(self):
        self.analysed_preview(text='Location: Dubai')
        self.assertEqual(self.confirm().status_code, 200)
        from ..services.document_intelligence import _extract_file_facts

        def conflicting_assertion(rows, source, **kwargs):
            _extract_file_facts(rows, source, **kwargs)
            rows['facts'].append(IntelligenceFact(
                run=rows['run'], source_file=source, fact_type='location', key='location',
                value='Abu Dhabi', normalized_value='abu dhabi', source_excerpt='Abu Dhabi',
                source_locator={'line': 2}, extraction_method='ai',
            ))

        with patch('apps.planning_intelligence.services.document_intelligence._extract_file_facts', conflicting_assertion):
            current, intelligence = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.assertEqual(current.conflicts.get(key='location:location').status, 'open')
        self.assertEqual(set(current.facts.filter(fact_type='location').values_list('status', flat=True)), {'conflicted'})
        self.assertIsNone(intelligence['detected_location'])
        self.assertNotIn('preview_confirmation', current.summary)

    def test_review_retention_and_new_facts_roll_back_if_audit_fails(self):
        self.analysed_preview()
        self.assertEqual(self.confirm().status_code, 200)
        with patch('apps.planning_intelligence.services.intelligence_review_retention.record_event',
                   side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        failed = self.project.intelligence_runs.first()
        self.assertEqual(failed.status, 'failed')
        self.assertFalse(failed.facts.exists())
        failed.refresh_from_db()
        self.assertNotIn('preview_confirmation', failed.summary)
