"""
CRS Documents URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CRSDocumentViewSet

router = DefaultRouter()
router.register(r'documents', CRSDocumentViewSet, basename='crs-document')

urlpatterns = [
    path('', include(router.urls)),
]
