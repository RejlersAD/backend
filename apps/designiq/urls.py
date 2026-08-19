"""
DesignIQ URLs - API Endpoints
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DesignProjectViewSet,
    DesignAnalysisViewSet,
    DesignOptimizationViewSet,
    DesignTemplateViewSet,
    EngineeringListItemViewSet
)

router = DefaultRouter()
router.register(r'projects', DesignProjectViewSet, basename='designiq-project')
router.register(r'analyses', DesignAnalysisViewSet, basename='designiq-analysis')
router.register(r'optimizations', DesignOptimizationViewSet, basename='designiq-optimization')
router.register(r'templates', DesignTemplateViewSet, basename='designiq-template')
router.register(r'lists', EngineeringListItemViewSet, basename='designiq-list')

urlpatterns = [
    path('', include(router.urls)),
]

