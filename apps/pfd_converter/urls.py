"""
PFD Converter URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import views_enhanced
from .history_views import (
    pfd_history_overview,
    pfd_all_uploads,
    pfd_all_conversions,
    download_pfd_file,
    download_converted_pid,
    delete_pfd_document
)

router = DefaultRouter()
router.register(r'documents', views.PFDDocumentViewSet, basename='pfd-document')
router.register(r'conversions', views.PIDConversionViewSet, basename='pid-conversion')
router.register(r'feedback', views.ConversionFeedbackViewSet, basename='conversion-feedback')

urlpatterns = [
    # History endpoints
    path('history/', pfd_history_overview, name='pfd-history-overview'),
    path('history/uploads/', pfd_all_uploads, name='pfd-history-uploads'),
    path('history/conversions/', pfd_all_conversions, name='pfd-history-conversions'),
    path('history/download/pfd/<int:pfd_id>/', download_pfd_file, name='pfd-history-download-pfd'),
    path('history/download/pid/<int:conversion_id>/', download_converted_pid, name='pfd-history-download-pid'),
    path('history/delete/pfd/<int:pfd_id>/', delete_pfd_document, name='pfd-history-delete-pfd'),
    
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
