"""
Project Management URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.project_views import (
    ProjectViewSet,
    ProjectTaskViewSet,
    ProjectMilestoneViewSet
)

router = DefaultRouter()
router.register(r'', ProjectViewSet, basename='project')
router.register(r'tasks', ProjectTaskViewSet, basename='project-task')
router.register(r'milestones', ProjectMilestoneViewSet, basename='project-milestone')

urlpatterns = [
    path('', include(router.urls)),
]
