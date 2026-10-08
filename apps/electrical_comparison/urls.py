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
)

urlpatterns = [
    path('projects/', ProjectsView.as_view()),
    path('projects/<uuid:project_id>/', ProjectDetailView.as_view()),
    path('projects/<uuid:project_id>/history/', ProjectHistoryView.as_view()),
    path('test-key/', TestAPIKeyView.as_view()),
    path('upload/', UploadComparisonView.as_view()),
    path('status/<uuid:job_id>/',
         JobStatusView.as_view()),
    path('results/<uuid:job_id>/',
         JobResultsView.as_view()),
    path('export/<uuid:job_id>/',
         ExportExcelView.as_view()),
]
