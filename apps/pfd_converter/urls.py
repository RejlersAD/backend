"""
PFD Converter URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import views_enhanced

router = DefaultRouter()
router.register(r'documents', views.PFDDocumentViewSet, basename='pfd-document')
router.register(r'conversions', views.PIDConversionViewSet, basename='pid-conversion')
router.register(r'feedback', views.ConversionFeedbackViewSet, basename='conversion-feedback')

urlpatterns = [
    path('', include(router.urls)),
    
    # S3 PFD/PID Integration
    path('s3/', include('apps.pfd.urls.s3_urls')),
    
    # AI-Assisted PFD to P&ID Conversion (6-Step Workflow)
    path('ai-assisted-conversion/', views_enhanced.ai_assisted_conversion, name='ai-assisted-conversion'),
    path('download-pid/<uuid:conversion_id>/', views_enhanced.download_pid_pdf, name='download-pid-pdf'),
    path('download-assumptions/<uuid:conversion_id>/', views_enhanced.download_assumptions_report, name='download-assumptions'),
    path('download-instruments/<uuid:conversion_id>/', views_enhanced.download_instrument_list, name='download-instruments'),
    path('download-valves/<uuid:conversion_id>/', views_enhanced.download_valve_list, name='download-valves'),
    path('conversion-status/<uuid:conversion_id>/', views_enhanced.conversion_status, name='conversion-status'),
]
