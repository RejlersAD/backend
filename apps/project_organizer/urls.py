"""Project Organizer — URL routing."""
from django.urls import path

from . import views

app_name = 'project_organizer'

urlpatterns = [
    path('projects/',                          views.projects_collection, name='projects'),
    path('projects/<uuid:project_id>/',         views.project_detail,      name='project-detail'),
    path('projects/<uuid:project_id>/activity/', views.activity_collection, name='project-activity'),
]
