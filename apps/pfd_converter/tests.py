"""F05 synthetic integrity checks through production views and route guards."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import include, path, resolve
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints
from . import artifacts, views
from .history_views import pfd_all_conversions, download_converted_pid
from .models import PFDDocument, PIDConversion
from .views_enhanced import download_pid_pdf

router = DefaultRouter()
router.register('conversions', views.PIDConversionViewSet, basename='pid-conversion')
urlpatterns = [
    path('api/v1/pfd/', include(router.urls)),
    path('api/v1/pfd/history/conversions/', pfd_all_conversions, name='pfd-history-conversions'),
    path('api/v1/pfd/history/download/pid/<uuid:conversion_id>/', download_converted_pid, name='pfd-history-download-pid'),
    path('api/v1/pfd/download-pid/<uuid:conversion_id>/', download_pid_pdf, name='download-pid-pdf'),
]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/pfd/conversions/'
ORIGINAL = b'%PDF-1.4\nsynthetic reviewed original\n%%EOF'
REPLACEMENT = b'%PDF-1.4\nsynthetic regenerated unreviewed\n%%EOF'


def synthetic_render(source, output_path):
    Path(output_path).write_bytes(REPLACEMENT)


@override_settings(ROOT_URLCONF=__name__, RADAI_BUSINESS_APPROVAL_ROUTES={})
class PFDArtifactIntegrityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(patch.stopall)
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('No live provider requests')).start()
        self.httpx = patch('httpx.Client.send', side_effect=AssertionError('No live provider requests')).start()
        self.addCleanup(self.network.assert_not_called)
        self.addCleanup(self.httpx.assert_not_called)
        self.user = get_user_model().objects.create_user('synthetic-pfd-owner', email='pfd@example.test')
        self.other = get_user_model().objects.create_user('synthetic-pfd-other', email='other@example.test')
        self.role = Role.objects.create(code='synthetic_pfd_editor', name='Synthetic PFD editor', level=5)
        self.module, _ = Module.objects.get_or_create(code='pfd_to_pid', defaults={'name': 'PFD conversion'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'create', 'update', 'export', 'approve']):
            RolePermission.objects.create(role=self.role, permission=permission)
        organization = Organization.objects.create(code='SYNTHETIC-PFD', name='Synthetic organization')
        for user in (self.user, self.other):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization, 'status': 'active'})
            UserRole.objects.create(user_profile=profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.document = PFDDocument.objects.create(uploaded_by=self.user, document_number='SYN-PFD', project_code='SYN')
        self.source = PIDConversion.objects.create(
            pfd_document=self.document, converted_by=self.user, pid_drawing_number='SYN-PID',
            pid_title='Synthetic drawing', pid_revision='A', status='approved',
            equipment_list=[{'tag': 'V-101', 'type': 'vessel', 'description': 'Synthetic vessel'}],
            instrument_list=[], piping_details=[], safety_systems=[],
            reviewed_by=self.user, reviewed_at=timezone.now(), review_notes='Synthetic historical review',
            conversion_data={'historical': {'evidence': 'preserve'}},
        )
        self.source.pid_file.save(f'{self.source.pk}.pdf', ContentFile(ORIGINAL))
        self.before = deepcopy(PIDConversion.objects.filter(pk=self.source.pk).values().get())
        self.storage = self.source.pid_file.storage
        self.addCleanup(self.cleanup_files)

    def cleanup_files(self):
        from django.conf import settings
        # Each test uses only the settings-owned temporary media directory.
        for suffix in ('*.pdf', '*.png'):
            for item in Path(settings.MEDIA_ROOT).rglob(suffix):
                item.unlink()

    def url(self, action='', conversion=None):
        return f'{BASE}{(conversion or self.source).pk}/{action + "/" if action else ""}'

    def tokens(self):
        self.source.refresh_from_db()
        return {'expected_updated_at': self.source.updated_at.isoformat(),
                'expected_artifact_sha256': artifacts.artifact_digest(artifacts.read_artifact(self.source))}

    def regenerate(self, data=None):
        return self.client.post(self.url('regenerate'), data if data is not None else self.tokens(), format='json')

    def assert_original_unchanged(self):
        self.source.refresh_from_db()
        self.assertEqual(PIDConversion.objects.filter(pk=self.source.pk).values().get(), self.before)
        self.assertEqual(artifacts.read_artifact(self.source), ORIGINAL)

    def test_repeated_downloads_do_not_generate_write_or_change_review(self):
        with patch.object(artifacts, 'render_existing_specifications') as render:
            for _ in range(3):
                response = self.client.get(self.url('download_drawing'))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, ORIGINAL)
                self.assertEqual(response['X-Output-Id'], str(self.source.pk))
            render.assert_not_called()
        self.assert_original_unchanged()

    def test_legacy_flags_are_explicit_and_never_mutate(self):
        for flag in ('true', '1', 'yes', 'garbage', ''):
            response = self.client.get(self.url('download_drawing'), {'force_regenerate': flag})
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.client.get(self.url('download_drawing'), {'force_regenerate': 'false'}).content, ORIGINAL)
        self.assert_original_unchanged()

    def test_guard_maps_regeneration_separately_from_download(self):
        self.assertTrue(issubclass(resolve(self.url('regenerate')).func.cls, ModuleActionGuardMixin))
        RolePermission.objects.filter(role=self.role, permission__action='update').delete()
        self.assertEqual(self.client.get(self.url('download_drawing')).status_code, 200)
        with patch.object(artifacts, 'render_existing_specifications') as render:
            self.assertEqual(self.regenerate().status_code, 403)
            render.assert_not_called()
        self.assert_original_unchanged()

    def test_scope_denies_other_person_without_team_visibility(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.regenerate().status_code, 404)
        self.assertEqual(self.client.get(self.url('download_drawing')).status_code, 404)
        self.assert_original_unchanged()

    def test_anonymous_is_denied(self):
        self.client.force_authenticate(None)
        self.assertIn(self.regenerate().status_code, (401, 403))
        self.assert_original_unchanged()

    def test_success_creates_distinct_unreviewed_output_and_keeps_original(self):
        PIDConversion.objects.filter(pk=self.source.pk).update(
            conversion_data={artifacts.INTEGRITY_KEY: {'sha256': artifacts.artifact_digest(ORIGINAL), 'reviewed_sha256': artifacts.artifact_digest(ORIGINAL)}},
            confidence_score=80, compliance_checks={'historical_pass': True})
        self.before = deepcopy(PIDConversion.objects.filter(pk=self.source.pk).values().get())
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render):
            response = self.regenerate()
        self.assertEqual(response.status_code, 201, response.data)
        child = PIDConversion.objects.get(pk=response.data['id'])
        self.assertNotEqual(child.pk, self.source.pk)
        self.assertNotEqual(child.pid_file.name, self.source.pid_file.name)
        self.assertEqual(artifacts.read_artifact(child), REPLACEMENT)
        self.assertEqual(child.status, 'completed')
        self.assertIsNone(child.reviewed_by)
        self.assertIsNone(child.reviewed_at)
        self.assertEqual(child.review_notes, '')
        self.assertIsNone(child.confidence_score)
        self.assertEqual(child.compliance_checks, {})
        self.assertNotIn('reviewed_sha256', artifacts.integrity_metadata(child))
        self.assertEqual(response.data['artifact']['review_state'], 'unreviewed')
        self.assertEqual(response.data['artifact']['source_conversion_id'], str(self.source.pk))
        self.assertEqual(self.client.get(self.url('download_drawing', child)).content, REPLACEMENT)
        self.assert_original_unchanged()

    def test_legacy_approval_is_preserved_but_not_retroactively_certified(self):
        result = self.client.get(self.url())
        self.assertEqual(result.data['status'], 'approved')
        self.assertEqual(result.data['artifact']['review_state'], 'legacy_approved')
        self.assertEqual(result.data['artifact']['sha256'], artifacts.artifact_digest(ORIGINAL))
        self.assert_original_unchanged()

    def test_missing_and_invalid_freshness_tokens_reject_before_render(self):
        for body in ({}, {'expected_updated_at': 'invalid'}, {**self.tokens(), 'expected_artifact_sha256': ''}):
            with patch.object(artifacts, 'render_existing_specifications') as render:
                self.assertEqual(self.regenerate(body).status_code, 400)
                render.assert_not_called()
        self.assert_original_unchanged()

    def test_stale_timestamp_and_fingerprint_reject(self):
        bodies = [{**self.tokens(), 'expected_updated_at': '2001-01-01T00:00:00Z'},
                  {**self.tokens(), 'expected_artifact_sha256': '0' * 64}]
        for body in bodies:
            with patch.object(artifacts, 'render_existing_specifications') as render:
                self.assertEqual(self.regenerate(body).status_code, 409)
                render.assert_not_called()
        self.assert_original_unchanged()

    def test_change_during_generation_rejects_and_removes_only_candidate(self):
        def render_and_change(source, path):
            synthetic_render(source, path)
            PIDConversion.objects.filter(pk=self.source.pk).update(review_notes='Concurrent review')
        with patch.object(artifacts, 'render_existing_specifications', side_effect=render_and_change):
            response = self.regenerate()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()  # Router rolls back this same-transaction synthetic change.

    def test_change_during_storage_is_rechecked(self):
        save = self.storage.save
        def store_then_change(name, content, **kwargs):
            result = save(name, content, **kwargs)
            PIDConversion.objects.filter(pk=self.source.pk).update(equipment_list=[{'tag': 'V-202'}])
            return result
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render), patch.object(self.storage, 'save', side_effect=store_then_change):
            response = self.regenerate()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_bytes_changed_during_generation_are_rechecked(self):
        def render_and_change(source, path):
            synthetic_render(source, path)
            with self.storage.open(self.source.pid_file.name, 'wb') as handle:
                handle.write(REPLACEMENT)
        with patch.object(artifacts, 'render_existing_specifications', side_effect=render_and_change):
            response = self.regenerate()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assertEqual(PIDConversion.objects.filter(pk=self.source.pk).values().get(), self.before)
        self.assertEqual(artifacts.read_artifact(self.source), REPLACEMENT)  # Concurrent actor's bytes, not overwritten.

    def test_generation_failure_preserves_original(self):
        with patch.object(artifacts, 'render_existing_specifications', side_effect=RuntimeError('synthetic provider-free render failure')):
            self.assertEqual(self.regenerate().status_code, 503)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_invalid_generated_pdf_does_not_publish(self):
        with patch.object(artifacts, 'render_existing_specifications', side_effect=lambda source, path: Path(path).write_bytes(b'not a PDF')):
            self.assertEqual(self.regenerate().status_code, 503)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_storage_failure_preserves_original(self):
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render), patch.object(self.storage, 'save', side_effect=OSError('synthetic storage failure')):
            self.assertEqual(self.regenerate().status_code, 503)
        self.assert_original_unchanged()

    def test_source_storage_failure_never_starts_regeneration(self):
        with patch.object(self.storage, 'open', side_effect=OSError('synthetic read failure')), patch.object(artifacts, 'render_existing_specifications') as render:
            self.assertEqual(self.client.get(self.url('download_drawing')).status_code, 503)
            response = self.regenerate({'expected_updated_at': self.source.updated_at.isoformat(), 'expected_artifact_sha256': artifacts.artifact_digest(ORIGINAL)})
            self.assertEqual(response.status_code, 503)
            render.assert_not_called()
        self.assert_original_unchanged()

    def test_missing_artifact_does_not_trigger_generation(self):
        self.storage.delete(self.source.pid_file.name)
        with patch.object(artifacts, 'render_existing_specifications') as render:
            self.assertEqual(self.client.get(self.url('download_drawing')).status_code, 404)
            details = self.client.get(self.url()).data
            self.assertFalse(details['artifact']['available'])
            self.assertEqual(details['allowed_actions'], [])
            render.assert_not_called()

    def test_parent_project_change_during_render_rejects(self):
        def render_and_change(source, path):
            synthetic_render(source, path)
            PFDDocument.objects.filter(pk=self.document.pk).update(project_name='Concurrent project change')
        with patch.object(artifacts, 'render_existing_specifications', side_effect=render_and_change):
            self.assertEqual(self.regenerate().status_code, 409)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_stored_valves_preserved_and_malformed_valves_unavailable(self):
        valves = '[{"tag":"HV-101","type":"gate"}]'
        PIDConversion.objects.filter(pk=self.source.pk).update(valve_list=valves)
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render):
            response = self.regenerate()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(PIDConversion.objects.get(pk=response.data['id']).valve_list, valves)
        PIDConversion.objects.filter(pk=self.source.pk).update(valve_list='unparseable recorded valves')
        with patch.object(artifacts, 'render_existing_specifications') as render:
            self.assertEqual(self.regenerate().status_code, 400)
            render.assert_not_called()

    def test_export_denial_applies_to_every_download_route(self):
        RolePermission.objects.filter(role=self.role, permission__action='export').delete()
        for path in (self.url('download_drawing'), f'/api/v1/pfd/history/download/pid/{self.source.pk}/', f'/api/v1/pfd/download-pid/{self.source.pk}/'):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assert_original_unchanged()

    def test_storage_readback_mismatch_does_not_publish(self):
        original_open = self.storage.open
        def open_artifact(name, mode='rb'):
            if 'regenerated/' in name:
                return BytesIO(b'corrupted candidate')
            return original_open(name, mode)
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render), patch.object(self.storage, 'open', side_effect=open_artifact):
            self.assertEqual(self.regenerate().status_code, 503)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_database_failure_removes_candidate_and_keeps_review(self):
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render), patch.object(PIDConversion.objects, 'create', side_effect=DatabaseError('synthetic DB failure')):
            self.assertEqual(self.regenerate().status_code, 503)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_failure_after_child_insert_rolls_back_publication(self):
        original_save = PIDConversion.save
        def save_then_fail(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            if instance.pk != self.source.pk:
                raise DatabaseError('synthetic persistence failure after insert')
        with patch.object(artifacts, 'render_existing_specifications', side_effect=synthetic_render), patch.object(PIDConversion, 'save', save_then_fail):
            self.assertEqual(self.regenerate().status_code, 503)
        self.assertEqual(PIDConversion.objects.count(), 1)
        self.assert_original_unchanged()

    def test_initial_generation_with_same_number_uses_distinct_output_paths(self):
        self.document.extracted_data = {'equipment': [{'tag': 'V-101', 'type': 'vessel'}]}
        self.document.save()
        specs = {'equipment_list': self.source.equipment_list, 'instrument_list': [], 'piping_specifications': [], 'safety_devices': []}
        pipeline = SimpleNamespace(convert=lambda **kwargs: {'pid_specifications': specs, 'drawing_path': self.source.pid_file.path})
        converter = SimpleNamespace(validate_conversion=lambda *args: {'compliance_score': 0})
        generated_paths = []
        def generate(**kwargs):
            output = kwargs['output_path']
            generated_paths.append(output)
            Path(output).write_bytes(b'%PDF-1.4\nsynthetic run ' + str(len(generated_paths)).encode() + b'\n%%EOF')
            return {'output_path': output}
        module = SimpleNamespace(generate_ultra_complete_pid=generate)
        body = {'pfd_document_id': str(self.document.pk), 'pid_drawing_number': self.source.pid_drawing_number, 'pid_title': 'Synthetic new output', 'pid_revision': 'A'}
        first = None
        with patch.object(views, 'AdvancedPFDToPIDPipeline', return_value=pipeline), patch.object(views, 'PFDToPIDConverter', return_value=converter), patch.dict('sys.modules', {'apps.pfd_converter.ultra_complete_service': module}):
            for _ in range(2):
                response = self.client.post(BASE + 'generate/', body, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertIn(response.data['id'], generated_paths[-1])
                if first is None:
                    first = PIDConversion.objects.get(pk=response.data['id'])
                    first.status, first.reviewed_by, first.reviewed_at, first.review_notes = 'approved', self.user, timezone.now(), 'Synthetic first review'
                    first.save()
                    first_record = deepcopy(PIDConversion.objects.filter(pk=first.pk).values().get())
                    first_bytes = artifacts.read_artifact(first)
        self.assertEqual(len(set(generated_paths)), 2)
        self.assertEqual(PIDConversion.objects.filter(pk=first.pk).values().get(), first_record)
        self.assertEqual(artifacts.read_artifact(first), first_bytes)
        self.assert_original_unchanged()

    def test_initial_intelligent_generation_preserves_earlier_same_number_review(self):
        generated_paths = []
        def generate(**kwargs):
            output = kwargs['output_path']
            generated_paths.append(output)
            Path(output).write_bytes(b'\x89PNG\r\n\x1a\nsynthetic run ' + str(len(generated_paths)).encode())
            return {'drawing_path': output, 'specifications': {}}
        module = SimpleNamespace(IntelligentPIDGenerator=lambda reference: SimpleNamespace(generate_complete_pid=generate))
        body = {'pfd_document_id': str(self.document.pk), 'reference_pid_path': self.source.pid_file.path,
                'pid_drawing_number': self.source.pid_drawing_number, 'pid_title': 'Synthetic intelligent output', 'pid_revision': 'A'}
        first = None
        with patch.dict('sys.modules', {'apps.pfd_converter.intelligent_pid_generator': module}):
            for _ in range(2):
                response = self.client.post(BASE + 'intelligent-generate/', body, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertIn(response.data['id'], generated_paths[-1])
                if first is None:
                    first = PIDConversion.objects.get(pk=response.data['id'])
                    first.status, first.reviewed_by, first.reviewed_at, first.review_notes = 'approved', self.user, timezone.now(), 'Synthetic first review'
                    first.save()
                    first_record = deepcopy(PIDConversion.objects.filter(pk=first.pk).values().get())
                    first_bytes = artifacts.read_artifact(first)
        self.assertEqual(len(set(generated_paths)), 2)
        self.assertEqual(PIDConversion.objects.filter(pk=first.pk).values().get(), first_record)
        self.assertEqual(artifacts.read_artifact(first), first_bytes)
        self.assert_original_unchanged()

    def test_absent_specifications_are_unavailable_without_fabrication(self):
        PIDConversion.objects.filter(pk=self.source.pk).update(equipment_list=[])
        with patch.object(artifacts, 'render_existing_specifications') as render:
            self.assertEqual(self.regenerate().status_code, 400)
            render.assert_not_called()
        self.assertEqual(PIDConversion.objects.count(), 1)

    def test_generic_update_cannot_replace_artifact_or_review(self):
        for body in ({'pid_file': 'another.pdf'}, {'equipment_list': []}, {'review_notes': ''}, {'pid_revision': 'B'}, {'status': 'completed'}):
            response = self.client.patch(self.url(), body, format='json')
            self.assertIn(response.status_code, (400, 403), response.data)
        self.assert_original_unchanged()

    def test_approval_without_configured_authority_remains_denied(self):
        self.assertEqual(self.client.post(self.url('approve'), self.tokens(), format='json').status_code, 403)
        self.assert_original_unchanged()

    @override_settings(RADAI_BUSINESS_APPROVAL_ROUTES={'pfd_to_pid.PIDConversion.approve': {'positions': ['engineering_manager'], 'pending_states': ['completed']}})
    def test_configured_approval_binds_exact_bytes_and_rejects_stale_review(self):
        EmployeeMaster.objects.create(user=self.user, employee_number='SYN-REVIEWER', email=self.user.email, designation='Engineering Manager', employment_status='active', join_date='2020-01-01')
        PIDConversion.objects.filter(pk=self.source.pk).update(status='completed', reviewed_by=None, reviewed_at=None, review_notes='')
        tokens = self.tokens()
        stale = {**tokens, 'expected_artifact_sha256': '0' * 64}
        self.assertEqual(self.client.post(self.url('approve'), stale, format='json').status_code, 409)
        response = self.client.post(self.url('approve'), {**tokens, 'review_notes': 'Reviewed exact synthetic bytes'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['artifact']['review_state'], 'approved')
        self.source.refresh_from_db()
        self.assertEqual(artifacts.integrity_metadata(self.source)['reviewed_sha256'], artifacts.artifact_digest(ORIGINAL))
        approved = deepcopy(PIDConversion.objects.filter(pk=self.source.pk).values().get())
        repeated = self.client.post(self.url('approve'), self.tokens(), format='json')
        self.assertIn(repeated.status_code, (400, 409))
        self.assertEqual(PIDConversion.objects.filter(pk=self.source.pk).values().get(), approved)

    def test_bound_artifact_tampering_is_blocked_not_newly_approved(self):
        PIDConversion.objects.filter(pk=self.source.pk).update(conversion_data={artifacts.INTEGRITY_KEY: {'sha256': '0' * 64, 'reviewed_sha256': '0' * 64}})
        self.assertEqual(self.client.get(self.url('download_drawing')).status_code, 409)
        details = self.client.get(self.url()).data
        self.assertEqual(details['artifact']['review_state'], 'integrity_mismatch')
        self.assertEqual(details['allowed_actions'], [])

    def test_history_and_enhanced_downloads_use_same_identity_and_bytes(self):
        for path in (f'/api/v1/pfd/history/download/pid/{self.source.pk}/', f'/api/v1/pfd/download-pid/{self.source.pk}/'):
            self.assertEqual(self.client.get(path).content, ORIGINAL)
            self.assertEqual(self.client.get(path, {'force_regenerate': 'true'}).status_code, 400)
        history = self.client.get('/api/v1/pfd/history/conversions/').data['data']['conversions'][0]
        self.assertEqual(history['artifact']['identity'], str(self.source.pk))
        self.assertEqual(history['pid_revision'], 'A')
        self.assertTrue(history['can_download'])
        self.assert_original_unchanged()

    def test_binary_legacy_artifact_download_is_read_only(self):
        self.source.pid_file = None
        self.source.pid_pdf = ORIGINAL
        self.source.save()
        self.assertEqual(self.client.get(self.url('download_drawing')).content, ORIGINAL)

    def test_local_existing_renderer_produces_real_pdf_without_provider(self):
        response = self.regenerate()
        self.assertEqual(response.status_code, 201, response.data)
        child = PIDConversion.objects.get(pk=response.data['id'])
        self.assertTrue(artifacts.read_artifact(child).startswith(b'%PDF-'))
        self.assert_original_unchanged()
