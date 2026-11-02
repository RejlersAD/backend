from django.urls import path

from .views import (
    ChecklistReportView, ChecklistView, ConfigView, ExtractionView,
    FindingPatchView, FindingsView, NodePatchView, ReviewView,
    RunAnalysisView, SheetImageView, TopologyView, WorkspaceView,
)

urlpatterns = [
    path('analyses/<uuid:analysis_id>/run/', RunAnalysisView.as_view()),
    path('projects/<uuid:project_id>/documents/<uuid:file_id>/workspace/',
         WorkspaceView.as_view()),
    path('sheets/<uuid:sheet_id>/image/', SheetImageView.as_view()),
    path('analyses/<uuid:analysis_id>/extraction/', ExtractionView.as_view()),
    path('nodes/<uuid:node_id>/', NodePatchView.as_view()),
    path('analyses/<uuid:analysis_id>/topology/', TopologyView.as_view()),
    path('analyses/<uuid:analysis_id>/findings/', FindingsView.as_view()),
    path('findings/<uuid:id>/', FindingPatchView.as_view()),
    path('analyses/<uuid:analysis_id>/checklist/', ChecklistView.as_view()),
    path('analyses/<uuid:analysis_id>/checklist-report.xlsx', ChecklistReportView.as_view()),
    path('projects/<uuid:project_id>/config/', ConfigView.as_view()),
    path('projects/<uuid:project_id>/review/', ReviewView.as_view()),
]
