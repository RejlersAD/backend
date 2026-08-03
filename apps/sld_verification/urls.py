"""
SLD Verification URL Configuration
"""
from django.urls import path
from . import views

app_name = 'sld_verification'

urlpatterns = [
    # Projects
    path('projects/',                          views.projects,       name='projects'),
    path('projects/<str:project_id>/',         views.project_detail, name='project-detail'),

    # Core pipeline
    path('upload-sld/',                        views.upload_sld,     name='upload-sld'),
    path('status/<str:document_id>/',          views.get_status,     name='status'),
    path('results/<str:document_id>/',         views.get_results,    name='results'),

    # Exports
    path('export/excel/<str:document_id>/',    views.export_excel,   name='export-excel'),
    path('export/pdf/<str:document_id>/',      views.export_pdf,     name='export-pdf'),

    # Management
    path('list/',                              views.list_documents,  name='list'),
    path('delete/<str:document_id>/',          views.delete_document, name='delete'),

    # Engineer review — finding overrides
    path('findings/<int:finding_id>/',         views.update_finding,  name='update-finding'),

    # Drawing page image renderer (for frontend overlay)
    path('drawing-image/<str:document_id>/<int:page_index>/', views.drawing_image, name='drawing-image'),
]
