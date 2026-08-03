"""
URL configuration for ML Detection API
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DetectionConfigViewSet, MLDetectionModelViewSet,
    RealTimeAlertViewSet, DetectionAnalyticsViewSet
)

router = DefaultRouter()
router.register(r'configs', DetectionConfigViewSet, basename='detection-config')
router.register(r'models', MLDetectionModelViewSet, basename='ml-model')
router.register(r'alerts', RealTimeAlertViewSet, basename='alert')
router.register(r'analytics', DetectionAnalyticsViewSet, basename='analytics')

urlpatterns = [
    path('', include(router.urls)),
]
