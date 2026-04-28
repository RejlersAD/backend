"""
PFD Quality Checker — URL Configuration
"""
from django.urls import path

from . import views

app_name = 'pfd_quality'

urlpatterns = [
    # Projects
    path('projects/',                          views.projects,        name='projects'),
    path('projects/<str:project_id>/',         views.project_detail,  name='project-detail'),

    # Core pipeline
    path('upload-pfd/',                        views.upload_pfd,      name='upload-pfd'),
    path('status/<str:document_id>/',          views.get_status,      name='status'),
    path('results/<str:document_id>/',         views.get_results,     name='results'),

    # Exports (always regenerated in-memory — no S3 redirect to avoid CORS issues)
    path('export/excel/<str:document_id>/',    views.export_excel,    name='export-excel'),
    path('export/pdf/<str:document_id>/',      views.export_pdf,      name='export-pdf'),

    # Drawing image — rasterised page for frontend overlay panel
    path('drawing-image/<str:document_id>/<int:page_index>/', views.drawing_image, name='drawing-image'),

    # Re-extract tag positions — refresh overlay markers for an existing document
    path('reextract/<str:document_id>/',       views.reextract_positions, name='reextract'),

    # Management
    path('list/',                              views.list_documents,  name='list'),
    path('delete/<str:document_id>/',          views.delete_document, name='delete'),

    # Engineer review — finding overrides
    path('findings/<int:finding_id>/',         views.update_finding,  name='update-finding'),
]
