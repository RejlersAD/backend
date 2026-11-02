"""
P&ID Verification V2 URL Configuration
"""
from django.urls import path
from . import views
from apps.pid_verification.piping_valve_mto_view import (
    extract_valve_mto_view, extract_valve_mto_status_view, test_valve_mto_api_key_view,
)

app_name = 'pid_verification_v2'

urlpatterns = [
    # Projects
    path('projects/',                          views.projects,       name='projects'),
    path('projects/<str:project_id>/',         views.project_detail, name='project-detail'),

    # Core pipeline
    path('upload-pid/',                        views.upload_pid,     name='upload-pid'),
    path('status/<str:document_id>/',          views.get_status,     name='status'),
    path('results/<str:document_id>/',         views.get_results,    name='results'),

    # Exports
    path('export/excel/<str:document_id>/',    views.export_excel,   name='export-excel'),
    path('export/pdf/<str:document_id>/',      views.export_pdf,     name='export-pdf'),

    # Management
    path('list/',                              views.list_documents,  name='list'),
    path('update/<str:document_id>/',          views.update_document, name='update'),
    path('delete/<str:document_id>/',          views.delete_document, name='delete'),
    path('reprocess/<str:document_id>/',       views.reprocess_document, name='reprocess'),

    # Engineer review — finding overrides
    path('findings/<int:finding_id>/',         views.update_finding,  name='update-finding'),

    # Extraction items (engine prompt §4)
    # summary/ MUST come before <entity_id>/ so 'summary' isn't captured as an id.
    path('documents/<str:document_id>/extraction/',                 views.document_extraction,         name='document-extraction'),
    path('documents/<str:document_id>/scene3d/',                    views.document_scene3d,            name='document-scene3d'),
    path('documents/<str:document_id>/extraction/summary/',         views.document_extraction_summary, name='document-extraction-summary'),
    path('documents/<str:document_id>/extraction/<int:entity_id>/', views.update_extraction_item,      name='update-extraction-item'),

    # Checklist (PRO-001 Rev A)
    path('documents/<str:document_id>/checklist/',                  views.document_checklist,          name='document-checklist'),
    path('documents/<str:document_id>/checklist/<str:item_no>/',    views.document_checklist_item,     name='document-checklist-item'),
    path('documents/<str:document_id>/checklist-report.xlsx',       views.document_checklist_report,   name='document-checklist-report'),
    path('documents/<str:document_id>/checklist-report/import/',    views.document_checklist_report_import, name='document-checklist-import'),
    path('documents/<str:document_id>/review-meta/',                views.document_review_meta,        name='document-review-meta'),
    path('projects/<str:project_id>/checklist/',                    views.project_checklist,           name='project-checklist'),
    path('projects/<str:project_id>/checklist-template/',           views.project_checklist_template_upload, name='project-checklist-template'),
    path('projects/<str:project_id>/checklist-report.xlsx',         views.project_checklist_report,    name='project-checklist-report'),

    # Legend-backed recognition controls
    path('legend-knowledge/',                  views.legend_knowledge,           name='legend-knowledge'),
    path('legend-knowledge/build/',            views.build_legend_knowledge_api, name='build-legend-knowledge'),
    path('compare/<str:document_id>/',         views.compare_accuracy,           name='compare-accuracy'),

    # Per-project legend management
    path('projects/<str:project_id>/legend/',        views.project_legend,       name='project-legend'),
    path('projects/<str:project_id>/legend/build/',  views.project_legend_build, name='project-legend-build'),

    # Legend Sheets — AI-powered upload & extraction
    path('projects/<str:project_id>/legend-sheets/',          views.project_legend_sheets, name='project-legend-sheets'),
    path('projects/<str:project_id>/legend-sheets/upload/',   views.upload_legend_sheet,   name='upload-legend-sheet'),
    path('legend-sheets/<str:legend_id>/',                    views.legend_sheet_detail,   name='legend-sheet-detail'),
    path('legend-sheets/<str:legend_id>/retry/',              views.retry_legend_extraction, name='retry-legend-extraction'),

    # Reference Data — line list, equipment list, instrument index uploads
    path('projects/<str:project_id>/reference-data/',         views.project_reference_data,  name='project-reference-data'),
    # Sheets + combined review (multi-file / multi-sheet, 30 Oct 2026)
    path('projects/<str:project_id>/sheets/',                 views.project_sheets,          name='project-sheets'),
    path('projects/<str:project_id>/review/',                 views.project_review,          name='project-review'),
    path('projects/<str:project_id>/findings/',               views.project_findings,        name='project-findings'),
    path('projects/<str:project_id>/cross-check/',            views.project_cross_check,     name='project-cross-check'),
    path('projects/<str:project_id>/report.xlsx',             views.project_report_excel,    name='project-report-excel'),
    path('sheets/<int:sheet_id>/',                            views.sheet_detail,            name='sheet-detail'),
    path('projects/<str:project_id>/reference-data/upload/',  views.upload_reference_data,   name='upload-reference-data'),
    path('reference-data/<str:reference_id>/',                views.reference_data_detail,   name='reference-data-detail'),

    # BYOK (Bring Your Own Key) — API Key Management
    path('projects/<str:project_id>/api-keys/',               views.manage_api_keys,         name='manage-api-keys'),
    path('projects/<str:project_id>/api-keys/<str:provider>/', views.delete_api_key,         name='delete-api-key'),

    # AI-Powered P&ID Checks
    path('projects/<str:project_id>/run-ai-checks/',          views.run_ai_checks,           name='run-ai-checks'),
    path('projects/<str:project_id>/ai-check-runs/',          views.project_ai_check_runs,   name='project-ai-check-runs'),
    path('ai-check-run/<str:run_id>/',                        views.ai_check_run_detail,     name='ai-check-run-detail'),

    # Instrument Symbol Registry
    path('projects/<str:project_id>/instrument-symbols/',     views.project_instrument_symbols, name='project-instrument-symbols'),
    path('instrument-symbols/<str:symbol_id>/',               views.instrument_symbol_detail,   name='instrument-symbol-detail'),

    # Tag Naming & Acronym Check
    path('check-naming/<str:document_id>/',    views.check_naming,               name='check-naming'),

    # Drawing page image renderer (for frontend overlay)
    path('drawing-image/<str:document_id>/<int:page_index>/', views.drawing_image, name='drawing-image'),

    # DCS / Instrument Symbol Compliance Analysis (AI — Gemini + OpenAI dual-chain)
    path('analyze-dcs/<str:document_id>/',                    views.analyze_dcs,   name='analyze-dcs'),

    # Piping — Valve MTO extraction (async job pattern)
    # test-key/ MUST come before <str:job_id>/ — see pid_verification's
    # own urls.py for why.
    path('extract-valve-mto/',                                extract_valve_mto_view,        name='extract-valve-mto'),
    path('extract-valve-mto/test-key/',                       test_valve_mto_api_key_view,   name='extract-valve-mto-test-key'),
    path('extract-valve-mto/<str:job_id>/',                   extract_valve_mto_status_view, name='extract-valve-mto-status'),
]
