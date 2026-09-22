"""The agreement HTTP workflow is scoped, asynchronous and safe to retry."""
from copy import deepcopy
import hashlib
from uuid import uuid4
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import path, resolve
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.rbac.models import Permission, UserPermissionOverride
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints
from apps.users.models import User
from ..agreement_models import AgreementWorkspace
from ..agreement_views import AgreementWorkspaceView
from ..models import PlanningAuditEvent, PlanningFile, PlanningJob, PlanningProject
from ..tasks import run_planning_job
from ..views import PlanningJobViewSet
from .test_agreement_workspace import AgreementWorkspaceTests, EXTRACT
from .test_business_approval_gates import grant_test_approval


urlpatterns = [
    path('api/v1/planning-intelligence/agreement-workspaces/create/', AgreementWorkspaceView.as_view(operation='create')),
    path('api/v1/planning-intelligence/agreement-workspaces/projects/<int:project_id>/', AgreementWorkspaceView.as_view()),
    *[path(f'api/v1/planning-intelligence/agreement-workspaces/projects/<int:project_id>/{operation}/',
           AgreementWorkspaceView.as_view(operation=operation)) for operation in ('analyze', 'accept')],
    path('api/v1/planning-intelligence/jobs/<int:pk>/', PlanningJobViewSet.as_view({'get': 'retrieve'})),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class AgreementWorkspaceAPITests(TestCase):
    candidate = AgreementWorkspaceTests.candidate
    analyze = AgreementWorkspaceTests.analyze

    def setUp(self):
        AgreementWorkspaceTests.setUp(self)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.url = f'/api/v1/planning-intelligence/agreement-workspaces/projects/{self.enterprise.pk}/'
        self.create_url = '/api/v1/planning-intelligence/agreement-workspaces/create/'

    def upload(self, *, content=b'Client: ADNOC', **extra):
        return {'file': SimpleUploadedFile('contract.txt', content, content_type='text/plain'),
                'idempotency_key': str(uuid4()), **extra}

    def extraction(self, project, files, **kwargs):
        source = files[0]
        with source.file.open('rb') as stream:
            raw = stream.read()
        text = raw.decode()
        sha = hashlib.sha256(raw).hexdigest()
        manifest = [{'file_id': source.pk, 'filename': source.original_filename, 'sha256': sha,
            'text_sha256': sha, 'storage_name': source.file.name, 'category': source.category,
            'updated_at': source.updated_at.isoformat(), 'page_count': 1, 'size_bytes': len(raw)}]
        candidate = {'id': 'client', 'tab': 'overview', 'field': 'client', 'entity_key': 'client',
            'label': 'Client', 'value': {'text': 'ADNOC'}, 'basis': 'document_fact', 'confidence': 'high',
            'sources': [{'file_id': source.pk, 'filename': source.original_filename, 'sha256': sha,
                'text_sha256': sha, 'quote': text, 'page': 1, 'char_start': 0, 'char_end': len(text), 'quote_verified': True}]}
        if kwargs.get('progress'):
            kwargs['progress']({'percent': 50, 'message': 'Reading source', 'phase': 'extracting'})
        return {'candidates': [candidate], 'document_manifest': manifest, 'coverage': {'status': 'complete'},
                'warnings': [], 'parsed_files': [{'file_id': source.pk, 'text': text, 'confidence': 0.9, 'coverage': {}}]}

    def test_get_does_not_create_workspace_or_jobs(self):
        self.assertTrue(issubclass(resolve(self.url).func.view_class, ModuleActionGuardMixin))
        enterprise = Project.objects.create(code='EMPTY-AGR', name='Empty project', owner=self.actor)
        response = self.client.get(f'/api/v1/planning-intelligence/agreement-workspaces/projects/{enterprise.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['planning_project_id'])
        self.assertIsNone(response.data['workspace'])
        self.assertFalse(PlanningProject.objects.filter(enterprise_project=enterprise).exists())
        self.assertFalse(PlanningJob.objects.exists())
        self.assertFalse(AgreementWorkspace.objects.exists())

    def test_read_denial_is_enforced_by_actual_central_route_guard(self):
        permission = Permission.objects.filter(module__code='planning_package', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.actor.rbac_profile, permission=permission, allowed=False)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AgreementWorkspace.objects.exists())

    @patch('apps.planning_intelligence.agreement_views.dispatch_job')
    def test_single_upload_creates_project_and_replay_never_duplicates_records(self, dispatch):
        key = str(uuid4())
        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(self.create_url, self.upload(idempotency_key=key), format='multipart')
            second = self.client.post(self.create_url, self.upload(idempotency_key=key), format='multipart')
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 202, second.data)
        self.assertEqual(first.data['job']['id'], second.data['job']['id'])
        self.assertEqual(first.data['enterprise_project_id'], second.data['enterprise_project_id'])
        self.assertEqual(Project.objects.count(), 2)
        self.assertEqual(PlanningProject.objects.count(), 2)
        self.assertEqual(PlanningFile.objects.count(), 2)
        self.assertEqual(PlanningJob.objects.count(), 1)
        dispatch.assert_called_once()
        changed = self.client.post(self.create_url, self.upload(content=b'A changed agreement', idempotency_key=key), format='multipart')
        self.assertEqual(changed.status_code, 409, changed.data)
        self.assertEqual(changed.data['code'], 'agreement_request_conflict')
        self.assertEqual(PlanningFile.objects.count(), 2)

    def test_new_project_name_is_replaced_only_when_marked_generated(self):
        self.enterprise.name = 'Agreement project'
        self.enterprise.custom_fields = {'agreement_setup': True}
        self.enterprise.save()
        self.project.name = 'Agreement project'
        self.project.save()
        workspace = self.analyze([self.candidate('project_name', {'text': 'Source project title'})])
        response = self.client.post(self.url + 'accept/', {'workspace_id': str(workspace.pk), 'revision': 1}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.enterprise.refresh_from_db()
        self.assertEqual(self.project.name, 'Source project title')
        self.assertEqual(self.enterprise.name, 'Source project title')

    def test_background_worker_creates_reviewable_draft_then_acceptance_populates_project(self):
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(self.url + 'analyze/', self.upload(), format='multipart')
        self.assertEqual(response.status_code, 202, response.data)
        job = PlanningJob.objects.get(pk=response.data['job']['id'])
        self.assertEqual(job.status, 'queued')
        self.assertFalse(AgreementWorkspace.objects.exists())
        with patch(EXTRACT, side_effect=self.extraction) as extract:
            result = run_planning_job.run(job.pk)
            replay = run_planning_job.run(job.pk)
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(replay['idempotent_replay'])
        extract.assert_called_once()
        self.assertEqual(AgreementWorkspace.objects.count(), 1)
        workspace = AgreementWorkspace.objects.get()
        self.project.refresh_from_db()
        self.assertEqual(self.project.client, '')
        response = self.client.get(self.url)
        self.assertTrue(response.data['permissions']['can_accept'])
        self.assertEqual(response.data['workspace']['id'], str(workspace.pk))
        self.assertIn('preview_url', response.data['workspace']['projection']['overview']['items'][0]['sources'][0])
        accepted = self.client.post(self.url + 'accept/', {'workspace_id': str(workspace.pk), 'revision': 1}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.client, 'ADNOC')
        self.assertEqual(accepted.data['workspace']['counts']['accepted'], 1)
        self.assertEqual(PlanningAuditEvent.objects.filter(action='agreement.accepted').count(), 1)

    def test_queue_detects_changed_selected_sources_before_extraction(self):
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(self.url + 'analyze/',
                {'file_ids': [self.source.pk], 'idempotency_key': str(uuid4())}, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        self.source.original_filename = 'replaced.txt'
        self.source.save()
        with patch(EXTRACT) as extract:
            run_planning_job.run(response.data['job']['id'])
        job = PlanningJob.objects.get(pk=response.data['job']['id'])
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.error_code, 'agreement_sources_changed')
        extract.assert_not_called()

    def test_project_boundaries_protect_sources_jobs_and_workspace(self):
        outsider = User.objects.create_user(username='agreement-api-outsider', email='outside-api@example.test')
        grant_test_approval((outsider,))
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(self.url + 'analyze/', self.upload(), format='multipart')
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url + 'analyze/', self.upload(), format='multipart').status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/planning-intelligence/jobs/{response.data['job']['id']}/").status_code, 404)
        self.client.force_authenticate(self.actor)
        other = PlanningProject.objects.create(name='Other source', created_by=self.actor)
        source = PlanningFile.objects.create(project=other, file='other.txt', original_filename='other.txt')
        PlanningJob.objects.all().update(status='cancelled')
        response = self.client.post(self.url + 'analyze/', {'file_ids': [source.pk], 'idempotency_key': str(uuid4())}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PlanningJob.objects.count(), 1)

    def test_viewer_can_read_but_cannot_analyze_or_accept_and_module_deny_is_effective(self):
        viewer = User.objects.create_user(username='agreement-api-viewer', email='viewer-api@example.test')
        grant_test_approval((viewer,))
        ProjectMember.objects.create(project=self.enterprise, user=viewer, role='viewer')
        self.client.force_authenticate(viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['permissions']['can_analyze'])
        self.assertEqual(self.client.post(self.url + 'analyze/', self.upload(), format='multipart').status_code, 403)
        self.assertEqual(self.client.post(self.url + 'accept/', {}, format='json').status_code, 403)
        self.client.force_authenticate(self.actor)
        permission = Permission.objects.filter(module__code='planning_package', action='update').first()
        UserPermissionOverride.objects.create(user_profile=self.actor.rbac_profile, permission=permission, allowed=False)
        self.assertEqual(self.client.post(self.url + 'analyze/', self.upload(), format='multipart').status_code, 403)

    def test_failed_validation_is_atomic_and_analysis_blocks_acceptance(self):
        before = Project.objects.count()
        response = self.client.post(self.create_url, self.upload(code=self.enterprise.code), format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Project.objects.count(), before)
        self.assertFalse(PlanningJob.objects.exists())
        workspace = self.analyze([self.candidate('client', {'text': 'ADNOC'})])
        job = PlanningJob.objects.create(project=self.project, job_type='agreement_setup', requested_by=self.actor)
        response = self.client.post(self.url + 'accept/', {'workspace_id': str(workspace.pk), 'revision': 1}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'agreement_analysis_running')
        self.assertFalse(PlanningAuditEvent.objects.filter(action='agreement.accepted').exists())
        second = self.client.post(self.url + 'analyze/', self.upload(), format='multipart')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(PlanningJob.objects.count(), 1)

    def test_keys_are_encrypted_and_never_present_in_response_job_or_audit(self):
        key = 'sk-ant-' + 'a' * 32
        request_key = str(uuid4())
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(self.create_url, self.upload(ai_api_key=key, idempotency_key=request_key), format='multipart')
        self.assertEqual(response.status_code, 202, response.data)
        self.assertNotIn(key, str(response.data))
        project = PlanningProject.objects.get(pk=response.data['planning_project_id'])
        self.assertNotEqual(project.ai_settings['api_key_encrypted'], key)
        self.assertNotIn(key, str(project.jobs.get().request_data))
        self.assertNotIn(key, str(list(project.audit_events.values())))
        response = self.client.post(self.create_url, self.upload(ai_api_key='sk-ant-' + 'b' * 32,
                                     idempotency_key=request_key), format='multipart')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'agreement_request_conflict')
