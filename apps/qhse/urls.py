"""
QHSE URL Configuration - Soft-coded routing
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    QHSERunningProjectViewSet,
    # QHSESpotCheckRegisterViewSet,  # Disabled per QHSE Manager decision
    QHSEAuditViewSet
)

# Soft-coded router configuration
router = DefaultRouter()
router.register(r'projects', QHSERunningProjectViewSet, basename='qhse-projects')
# router.register(r'spot-checks', QHSESpotCheckRegisterViewSet, basename='qhse-spot-checks')  # Disabled
router.register(r'audits', QHSEAuditViewSet, basename='qhse-audits')

app_name = 'qhse'

urlpatterns = [
    path('', include(router.urls)),
    # AI/ML endpoints
    path('ai/', include('apps.qhse.ai_urls')),
]
