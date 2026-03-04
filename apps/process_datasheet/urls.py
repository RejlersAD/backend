"""
Process Datasheet URLs
API endpoint routing
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EquipmentTypeViewSet,
    ProcessDatasheetViewSet,
    DatasheetTemplateViewSet,
    DatasheetValidationRuleViewSet,
    DatasheetExtractionJobViewSet,
    PumpCalculationDataViewSet
)
from .sdv_streams_view import extract_sdv_streams, check_sdv_job_status

router = DefaultRouter()
router.register(r'equipment-types', EquipmentTypeViewSet, basename='equipment-type')
router.register(r'datasheets', ProcessDatasheetViewSet, basename='datasheet')
router.register(r'templates', DatasheetTemplateViewSet, basename='datasheet-template')
router.register(r'validation-rules', DatasheetValidationRuleViewSet, basename='validation-rule')
router.register(r'extraction-jobs', DatasheetExtractionJobViewSet, basename='extraction-job')
router.register(r'pump-calculations', PumpCalculationDataViewSet, basename='pump-calculation')

urlpatterns = [
    # Specific paths MUST come before router.urls to avoid conflicts
    path('datasheets/extract-sdv-streams/', extract_sdv_streams, name='extract-sdv-streams'),
    path('sdv-job-status/<str:job_id>/', check_sdv_job_status, name='check-sdv-job-status'),
    path('', include(router.urls)),
]
