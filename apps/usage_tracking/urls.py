"""
Usage Tracking URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UsageTrackingViewSet

app_name = 'usage_tracking'

router = DefaultRouter()
router.register(r'usage', UsageTrackingViewSet, basename='usage')

urlpatterns = [
    path('', include(router.urls)),
]
