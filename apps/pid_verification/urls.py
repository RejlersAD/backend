"""
P&ID Verification — URL Configuration (projects registry only).

The V1 analysis pipeline was removed (see docs/pidv1-removal-inventory.md).
Only the shared project-registry endpoints remain, because P&ID Checker V2
uses them via the frontend `pidProjectsService.js`.
"""
from django.urls import path

from . import projects_api

app_name = 'pid_verification'

urlpatterns = [
    path('projects/', projects_api.projects, name='projects'),
    path('projects/<str:project_id>/', projects_api.project_detail, name='project-detail'),
    path('list/', projects_api.list_documents, name='list'),
]
