"""
URL Configuration for Electrical Checklist API
Professional project-based system with full REST API
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ChecklistExtractionViewSet
from .project_views import ChecklistProjectViewSet

router = DefaultRouter()
router.register(r'projects', ChecklistProjectViewSet, basename='projects')
router.register(r'', ChecklistExtractionViewSet, basename='checklist')

app_name = 'electrical_checklist'

urlpatterns = [
    path('', include(router.urls)),
]
