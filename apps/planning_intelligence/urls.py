"""URL routes for the RADAI Project Planning Application — mounted at
/api/v1/planning-intelligence/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import PlanningFileViewSet, PlanningGenerationViewSet, PlanningProjectViewSet

router = DefaultRouter()
router.register(r'projects', PlanningProjectViewSet, basename='planning-project')
router.register(r'files', PlanningFileViewSet, basename='planning-file')
router.register(r'generations', PlanningGenerationViewSet, basename='planning-generation')

app_name = 'planning_intelligence'

urlpatterns = [
    path('', include(router.urls)),
]
