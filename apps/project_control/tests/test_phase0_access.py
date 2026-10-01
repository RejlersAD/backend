from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..models import Estimate
from ..views import EstimateViewSet


# Exercise the object-access layer independently of the shared router, whose
# callbacks the separate module/action guard suites wrap during test discovery.
router = DefaultRouter()
router.register('estimates', EstimateViewSet, basename='phase0-test-estimate')
urlpatterns = [path('api/v1/project-control/', include(router.urls))]


@override_settings(ROOT_URLCONF=__name__)
class ProjectControlAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='pc-owner', email='pc-owner@example.com')
        self.viewer = User.objects.create_user(username='pc-viewer', email='pc-viewer@example.com')
        self.outsider = User.objects.create_user(username='pc-outsider', email='pc-outsider@example.com')
        self.project = Project.objects.create(code='PC-001', name='Controls Project', owner=self.owner)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.estimate = Estimate.objects.create(project=self.project, title='Baseline')

    def test_outsider_cannot_list_project_estimates(self):
        client = APIClient()
        client.force_authenticate(self.outsider)
        response = client.get('/api/v1/project-control/estimates/')
        rows = response.data.get('results', response.data)
        self.assertEqual(rows, [])

    def test_viewer_can_read_but_cannot_approve_estimate(self):
        client = APIClient()
        client.force_authenticate(self.viewer)
        detail = client.get(f'/api/v1/project-control/estimates/{self.estimate.id}/')
        self.assertEqual(detail.status_code, 200)
        denied = client.post(f'/api/v1/project-control/estimates/{self.estimate.id}/approve/')
        self.assertEqual(denied.status_code, 403)
