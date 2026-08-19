"""
URL Configuration for Activity Tracking API
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SystemActivityViewSet,
    ActivityStreamViewSet,
    ActivityStatisticsViewSet,
    UserSessionViewSet,
)

app_name = 'activity'

# Create router for ViewSets
router = DefaultRouter()
router.register(r'activities', SystemActivityViewSet, basename='activity')
router.register(r'streams', ActivityStreamViewSet, basename='stream')
router.register(r'statistics', ActivityStatisticsViewSet, basename='statistics')
router.register(r'sessions', UserSessionViewSet, basename='session')

urlpatterns = [
    path('', include(router.urls)),
]
