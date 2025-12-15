"""
PFD Converter URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'documents', views.PFDDocumentViewSet, basename='pfd-document')
router.register(r'conversions', views.PIDConversionViewSet, basename='pid-conversion')
router.register(r'feedback', views.ConversionFeedbackViewSet, basename='conversion-feedback')

urlpatterns = [
    path('', include(router.urls)),
]
