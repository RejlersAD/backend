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
from .hmb_extractor_view import (
    extract_hmb_data,
    analyze_hmb_master_template_view,
    list_hmb_master_templates_view,
    retrieve_hmb_master_template_view,
    preview_hmb_case_files_view,
    execute_hmb_case_preview_view,
    import_hmb_case_files_view,
    hmb_project_consolidated_summary_view,
    hmb_project_records_preview_view,
    hmb_project_stream_comparison_view,
    hmb_project_stream_export_view,
)

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
    # HMB Extractor — standalone stream data extraction (additive, no P&ID coupling)
    path('datasheets/extract-hmb/', extract_hmb_data, name='extract-hmb'),
    path('datasheets/analyze-hmb-master-template/', analyze_hmb_master_template_view, name='analyze-hmb-master-template'),
    path('datasheets/hmb-master-templates/', list_hmb_master_templates_view, name='hmb-master-templates-list'),
    path('datasheets/hmb-master-templates/<uuid:profile_id>/', retrieve_hmb_master_template_view, name='hmb-master-templates-detail'),
    path('datasheets/preview-hmb-cases/', preview_hmb_case_files_view, name='preview-hmb-cases'),
    path('datasheets/execute-hmb-cases/', execute_hmb_case_preview_view, name='execute-hmb-cases'),
    path('datasheets/import-hmb-cases/', import_hmb_case_files_view, name='import-hmb-cases'),
    path('datasheets/hmb-projects/<uuid:project_id>/summary/', hmb_project_consolidated_summary_view, name='hmb-project-summary'),
    path('datasheets/hmb-projects/<uuid:project_id>/records-preview/', hmb_project_records_preview_view, name='hmb-project-records-preview'),
    path('datasheets/hmb-projects/<uuid:project_id>/stream-comparison/', hmb_project_stream_comparison_view, name='hmb-project-stream-comparison'),
    path('datasheets/hmb-projects/<uuid:project_id>/stream-export/', hmb_project_stream_export_view, name='hmb-project-stream-export'),
    path('', include(router.urls)),
]
