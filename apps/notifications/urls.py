"""
Notification System URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'notifications'

# DRF Router
router = DefaultRouter()
router.register(r'', views.NotificationViewSet, basename='notification')
router.register(r'categories', views.NotificationCategoryViewSet, basename='category')
router.register(r'preferences', views.NotificationPreferenceViewSet, basename='preference')
router.register(r'logs', views.NotificationLogViewSet, basename='log')

urlpatterns = [
    path('', include(router.urls)),
]
