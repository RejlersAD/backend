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
from .mov_equipment_view import extract_mov_equipment, check_mov_job_status
from .smart_datasheet_view import smart_datasheet_upload, smart_datasheet_status, smart_datasheet_preview
from .pump_hydraulic_view import extract_pump_hydraulic_view
from .pump_hydraulic_snapshot import PumpHydraulicSnapshotViewSet

router = DefaultRouter()
router.register(r'equipment-types', EquipmentTypeViewSet, basename='equipment-type')
router.register(r'datasheets', ProcessDatasheetViewSet, basename='datasheet')
router.register(r'templates', DatasheetTemplateViewSet, basename='datasheet-template')
router.register(r'validation-rules', DatasheetValidationRuleViewSet, basename='validation-rule')
router.register(r'extraction-jobs', DatasheetExtractionJobViewSet, basename='extraction-job')
router.register(r'pump-calculations', PumpCalculationDataViewSet, basename='pump-calculation')
router.register(r'pump-hydraulic-snapshots', PumpHydraulicSnapshotViewSet, basename='pump-hydraulic-snapshot')

urlpatterns = [
    # Specific paths MUST come before router.urls to avoid conflicts
    # Smart Datasheet endpoints (unified tool for all 4 types)
    path('datasheets/smart-upload/', smart_datasheet_upload, name='smart-datasheet-upload'),
    path('datasheets/smart-preview/', smart_datasheet_preview, name='smart-datasheet-preview'),
    path('smart-job-status/<str:job_id>/', smart_datasheet_status, name='smart-datasheet-status'),
    # SDV Streams endpoints
    path('datasheets/extract-sdv-streams/', extract_sdv_streams, name='extract-sdv-streams'),
    path('sdv-job-status/<str:job_id>/', check_sdv_job_status, name='check-sdv-job-status'),
    # MOV Equipment endpoints
    path('datasheets/extract-mov-equipment/', extract_mov_equipment, name='extract-mov-equipment'),
    path('mov-job-status/<str:job_id>/', check_mov_job_status, name='check-mov-job-status'),
    # Pump Hydraulic — synchronous form-prefill extractor (additive)
    path('datasheets/extract-pump-hydraulic/', extract_pump_hydraulic_view, name='extract-pump-hydraulic'),
    path('', include(router.urls)),
]
