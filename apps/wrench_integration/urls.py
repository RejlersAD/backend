"""
Wrench Integration – URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import WrenchConfigViewSet, WrenchSyncViewSet

router = DefaultRouter()
router.register(r'config', WrenchConfigViewSet, basename='wrench-config')
router.register(r'sync', WrenchSyncViewSet, basename='wrench-sync')

app_name = 'wrench_integration'

urlpatterns = [
    path('', include(router.urls)),
]
