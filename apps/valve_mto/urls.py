"""Valve MTO — URL routing."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ValveMTOLegendViewSet, ValveMTOProjectViewSet, fluid_codes_view, job_detail_view,
)

app_name = 'valve_mto'

router = DefaultRouter()
router.register('projects', ValveMTOProjectViewSet, basename='valve-mto-project')
router.register('legends', ValveMTOLegendViewSet, basename='valve-mto-legend')

urlpatterns = [
    path('fluid-codes/', fluid_codes_view, name='valve-mto-fluid-codes'),
    path('projects/<uuid:project_id>/jobs/<uuid:job_id>/', job_detail_view,
         name='valve-mto-job-detail'),
    path('', include(router.urls)),
]
