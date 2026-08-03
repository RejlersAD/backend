"""
PFD Project URLs
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ..views.project_views import PFDProjectViewSet
from ..views.verification_views import PFDVerificationViewSet

router = DefaultRouter()
router.register(r'projects', PFDProjectViewSet, basename='pfd-projects')
router.register(r'verify', PFDVerificationViewSet, basename='pfd-verify')

urlpatterns = router.urls
