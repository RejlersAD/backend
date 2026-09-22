"""Bulk review stays asynchronous, revision checked and project scoped."""
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.test import APIClient

from apps.core.project_models import ProjectMember
from apps.rbac.models import Permission, UserPermissionOverride
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User

from ..evidence_models import EvidenceDecision
from ..evidence_views import EvidenceReviewView
from ..models import PlanningJob
from ..services.evidence_graph import EvidenceError, evidence_review, refresh_evidence_graph
from ..tasks import run_planning_job
from ..views import PlanningJobViewSet
from .test_business_approval_gates import grant_test_approval
from .test_document_evidence_api import DocumentEvidenceAPITests


urlpatterns = [
    path('api/v1/planning-intelligence/projects/<int:project_id>/evidence-review/', EvidenceReviewView.as_view()),
    path('api/v1/planning-intelligence/projects/<int:project_id>/evidence-review/bulk/', EvidenceReviewView.as_view(operation='bulk')),
    path('api/v1/planning-intelligence/jobs/<int:pk>/', PlanningJobViewSet.as_view({'get': 'retrieve'})),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class BulkEvidenceAPITests(TestCase):
    def setUp(self):
        DocumentEvidenceAPITests.setUp(self)
        self.file.file.save('requirements.csv', ContentFile(b'Preserved original bytes'), save=True)
        self.graph = refresh_evidence_graph(self.project, self.user)
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/evidence-review/'
        self.payload = {'revision': self.graph.revision, 'mode': 'verified',
                        'reason': 'Authorize bulk acceptance of verified source values.'}

    def post(self, **updates):
        return self.client.post(self.url + 'bulk/', {**self.payload, **updates}, format='json')

    @patch('apps.planning_intelligence.evidence_views.dispatch_job')
    def test_returns_job_without_scanning_or_writing_facts_and_duplicate_click_reuses_it(self, dispatch):
        for permission in Permission.objects.filter(module__code='planning_package', action__in=['create', 'approve']):
            UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission, allowed=False)
        with patch('apps.planning_intelligence.services.evidence_graph._knowledge', side_effect=AssertionError('HTTP enqueue must not scan 10k facts')):
            with self.captureOnCommitCallbacks(execute=True):
                first, second = self.post(), self.post()
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 202, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(first.data['job_type'], 'evidence_bulk')
        self.assertEqual(first.data['status'], 'queued')
        self.assertEqual(PlanningJob.objects.count(), 1)
        self.assertFalse(EvidenceDecision.objects.exists())
        dispatch.assert_called_once()

    @patch('apps.planning_intelligence.evidence_views.dispatch_job')
    def test_rejects_stale_revision_or_sources_without_enqueue(self, dispatch):
        response = self.post(revision=self.graph.revision + 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'evidence_revision_conflict')
        self.file.extracted_text += '\nRevised source'
        self.file.save(update_fields=['extracted_text'])
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'evidence_sources_changed')
        self.assertFalse(PlanningJob.objects.exists())
        dispatch.assert_not_called()

    @patch('apps.planning_intelligence.services.evidence_bulk_ai.ai_availability', return_value={'available': False, 'reason': 'Configure project AI first.'})
    def test_ai_unavailable_is_explicit_and_verified_mode_remains_available(self, _availability):
        response = self.post(mode='ai_verified')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'evidence_ai_unavailable')
        self.assertFalse(PlanningJob.objects.exists())
        with self.captureOnCommitCallbacks(execute=False):
            response = self.post()
        self.assertEqual(response.status_code, 202)

    def test_project_viewer_and_module_denied_user_cannot_enqueue(self):
        viewer = User.objects.create_user(username='bulk-viewer', email='bulk-viewer@example.test')
        grant_test_approval((viewer,))
        ProjectMember.objects.create(project=self.project.enterprise_project, user=viewer, role='reviewer')
        self.client.force_authenticate(viewer)
        self.assertEqual(self.post().status_code, 403)
        self.client.force_authenticate(self.user)
        permission = Permission.objects.filter(module__code='planning_package', action='update').first()
        UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission, allowed=False)
        self.assertEqual(self.post().status_code, 403)
        self.assertFalse(PlanningJob.objects.exists())

    def test_global_summary_and_group_filter_keep_global_readiness(self):
        response = self.client.get(self.url, {'limit': 1})
        self.assertEqual(response.status_code, 200, response.data)
        summary = response.data['bulk_review']
        self.assertEqual(summary['total_open'], response.data['summary']['open_issue_count'])
        self.assertGreater(summary['total_open'], len(response.data['issues']))
        group = next(item for item in summary['unresolved_groups'] if item['count'])
        filtered = self.client.get(self.url, {'group': group['key'], 'limit': 200})
        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertEqual(filtered.data['pagination']['total'], group['count'])
        self.assertEqual(filtered.data['pagination']['overall_total'], summary['total_open'])
        self.assertEqual(filtered.data['readiness'], response.data['readiness'])
        self.assertEqual(filtered.data['bulk_review']['total_open'], summary['total_open'])

    def test_active_job_can_resume_but_other_project_jobs_cannot_be_polled(self):
        with self.captureOnCommitCallbacks(execute=False):
            created = self.post()
        response = self.client.get(self.url)
        self.assertEqual(response.data['bulk_review']['active_job']['id'], created.data['id'])
        other = User.objects.create_user(username='bulk-outsider', email='bulk-outsider@example.test')
        grant_test_approval((other,))
        outsider_client = APIClient()
        outsider_client.force_authenticate(other)
        self.assertEqual(outsider_client.get(f"/api/v1/planning-intelligence/jobs/{created.data['id']}/").status_code, 404)

    @patch('apps.planning_intelligence.evidence_views.dispatch_job')
    def test_failed_job_retry_is_explicit_and_reuses_durable_identity(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post()
        job = PlanningJob.objects.get(pk=response.data['id'])
        job.status, job.error_code = 'failed', 'queue_unavailable'
        job.result_data = {'progress_context': {'processed': 5000, 'total': 10778}}
        job.progress_log = [{'phase': 'previous_attempt'}]
        job.save(update_fields=['status', 'error_code', 'result_data', 'progress_log'])
        before = self.client.get(self.url)
        self.assertEqual(before.data['bulk_review']['latest_job']['status'], 'failed')
        self.assertEqual(dispatch.call_count, 1)
        with self.captureOnCommitCallbacks(execute=True):
            retry = self.post()
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(retry.data['id'], job.pk)
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(PlanningJob.objects.count(), 1)
        self.assertEqual(retry.data['result_data'], {})
        self.assertEqual(retry.data['progress_log'], [])
        self.assertIsNone(retry.data['started_at'])
        self.assertIsNone(retry.data['heartbeat_at'])

    @patch('apps.planning_intelligence.services.evidence_bulk.run_bulk_evidence_review')
    def test_worker_persists_result_progress_and_actionable_stale_error(self, run_bulk):
        job = PlanningJob.objects.create(project=self.project, requested_by=self.user, job_type='evidence_bulk', request_data=self.payload)
        def complete(project, actor, data, *, progress_callback, job):
            progress_callback({'progress': 45, 'message': 'Validated 5,000 source values', 'phase': 'validation',
                               'details': {'processed': 5000, 'total': 10778}})
            return {'graph_id': str(self.graph.pk), 'result_revision': self.graph.revision,
                    'counts': {'accepted_verified': 12, 'accepted_ai': 2, 'unresolved': 3}, 'review_complete': False}
        run_bulk.side_effect = complete
        result = run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(job.progress, 100)
        self.assertIn('14 values accepted', job.message)
        self.assertEqual(job.result_data['counts']['unresolved'], 3)
        self.assertTrue(any(entry['phase'] == 'validation' for entry in job.progress_log))
        run_planning_job.run(job.pk)
        self.assertEqual(run_bulk.call_count, 1)
        second = PlanningJob.objects.create(project=self.project, requested_by=self.user, job_type='evidence_bulk', request_data=self.payload)
        run_bulk.side_effect = EvidenceError('Sources changed; refresh evidence.', 'evidence_sources_changed')
        run_planning_job.run(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, 'failed')
        self.assertEqual(second.error_code, 'evidence_sources_changed')
        self.assertEqual(second.error_message, 'Sources changed; refresh evidence.')
        self.assertFalse(EvidenceDecision.objects.exists())

    def test_completed_result_is_only_offered_for_the_same_graph_revision(self):
        job = PlanningJob.objects.create(project=self.project, requested_by=self.user, job_type='evidence_bulk', status='succeeded',
                                        result_data={'graph_id': str(self.graph.pk), 'result_revision': self.graph.revision})
        current = self.client.get(self.url)
        self.assertEqual(current.data['bulk_review']['latest_job']['id'], job.pk)
        self.graph.revision += 1
        self.graph.save(update_fields=['revision'])
        self.assertIsNone(self.client.get(self.url).data['bulk_review']['latest_job'])
