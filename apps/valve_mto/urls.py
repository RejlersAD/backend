"""Valve MTO — URL routing."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ValveMTOProjectViewSet, fluid_codes_view

app_name = 'valve_mto'

router = DefaultRouter()
router.register('projects', ValveMTOProjectViewSet, basename='valve-mto-project')

urlpatterns = [
    path('fluid-codes/', fluid_codes_view, name='valve-mto-fluid-codes'),
    path('', include(router.urls)),
]
