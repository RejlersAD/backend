from django.urls import path
from .views import (
    TestAPIKeyView,
    UploadComparisonView,
    JobStatusView,
    JobResultsView,
    ExportExcelView,
    ProjectsView,
    ProjectDetailView,
    ProjectHistoryView,
    ProjectCombinedResultsView,
    ProjectCommentsView,
    AnnotatedPDFView,
    AnnotatedPagesView,
)

urlpatterns = [
    path('projects/', ProjectsView.as_view()),
    path('projects/<uuid:project_id>/', ProjectDetailView.as_view()),
    path('projects/<uuid:project_id>/history/', ProjectHistoryView.as_view()),
    path('projects/<uuid:project_id>/combined-results/', ProjectCombinedResultsView.as_view(), name='project-combined-results'),
    path('projects/<uuid:project_id>/comments/', ProjectCommentsView.as_view(), name='project-comments'),
    path('test-key/', TestAPIKeyView.as_view()),
    path('upload/', UploadComparisonView.as_view()),
    path('status/<uuid:job_id>/',
         JobStatusView.as_view()),
    path('results/<uuid:job_id>/',
         JobResultsView.as_view()),
    path('export/<uuid:job_id>/',
         ExportExcelView.as_view()),
    path('jobs/<uuid:job_id>/annotated-pdf/',
         AnnotatedPDFView.as_view(), name='annotated-pdf'),
    path('jobs/<uuid:job_id>/annotated-pages/',
         AnnotatedPagesView.as_view(), name='annotated-pages'),
]

# ── Staged analysis flow ("RADAI SLD Comparison" canvas) ─────────────────
from .views_staged import (  # noqa: E402
    SldAnalyseView, SldAnalysesView, SldAnalysisDetailView, SldAnalysisExportView,
    SldFileMappingView, SldFileUploadView, SldLegendView, SldStateView,
    SldUserAIKeyTestView, SldUserAIKeyView,
)

urlpatterns += [
    path('sld/user/ai-key/', SldUserAIKeyView.as_view()),
    path('sld/user/ai-key/test/', SldUserAIKeyTestView.as_view()),
    path('sld/<uuid:project_id>/state/', SldStateView.as_view()),
    path('sld/<uuid:project_id>/files/', SldFileUploadView.as_view()),
    path('sld/<uuid:project_id>/files/<uuid:file_id>/mapping/', SldFileMappingView.as_view()),
    path('sld/<uuid:project_id>/legend/', SldLegendView.as_view()),
    path('sld/<uuid:project_id>/analyse/', SldAnalyseView.as_view()),
    path('sld/<uuid:project_id>/analyses/', SldAnalysesView.as_view()),
    path('sld/<uuid:project_id>/analyses/<uuid:analysis_id>/', SldAnalysisDetailView.as_view()),
    path('sld/<uuid:project_id>/analyses/<uuid:analysis_id>/export/', SldAnalysisExportView.as_view()),
]
