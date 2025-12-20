from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.core.cors_test_views import CorsTestView, cors_health_check
from django.http import HttpResponse

# Import PID analysis models and services for export functionality
from apps.pid_analysis.models import PIDDrawing
from apps.pid_analysis.export_service import PIDReportExportService

# Import feature registry views
from apps.api.feature_views import list_features, get_feature, get_categories, get_navigation


def pid_export_view(request, pk):
    """Plain Django view for export - no DRF decorators"""
    
    print(f"\n{'='*60}")
    print(f"[PID EXPORT] Request received!")
    print(f"[PID EXPORT] PK: {pk}")
    print(f"[PID EXPORT] Method: {request.method}")
    print(f"[PID EXPORT] Path: {request.path}")
    print(f"{'='*60}\n")
    
    try:
        drawing = PIDDrawing.objects.get(id=pk)
        print(f"[PID EXPORT] Drawing found: {drawing.drawing_number}")
    except PIDDrawing.DoesNotExist:
        return HttpResponse('{"error": "Drawing not found"}', status=404, content_type='application/json')
    
    if not hasattr(drawing, 'analysis_report'):
        return HttpResponse('{"error": "No analysis report"}', status=404, content_type='application/json')
    
    export_format = request.GET.get('format', 'pdf')
    print(f"[PID EXPORT] Format: {export_format}")
    
    export_service = PIDReportExportService()
    
    try:
        if export_format == 'pdf':
            return export_service.export_pdf(drawing)
        elif export_format == 'excel':
            return export_service.export_excel(drawing)
        elif export_format == 'csv':
            return export_service.export_csv(drawing)
        else:
            return HttpResponse('{"error": "Invalid format"}', status=400, content_type='application/json')
    except Exception as e:
        print(f"[PID EXPORT ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return HttpResponse(f'{{"error": "{str(e)}"}}', status=500, content_type='application/json')

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # EXPORT ENDPOINT - plain Django view, no DRF
    path('api/v1/pid/simple-test-pk/<int:pk>/', pid_export_view, name='pid-export'),
    
    # CORS diagnostic endpoints (no auth required)
    path('api/v1/cors-test/', CorsTestView.as_view(), name='cors-test'),
    path('api/v1/cors/health/', cors_health_check, name='cors-health'),
    
    # Feature Registry API (Dynamic Feature Discovery)
    path('api/v1/features/', list_features, name='list-features'),
    path('api/v1/features/<str:feature_id>/', get_feature, name='get-feature'),
    path('api/v1/features/meta/categories/', get_categories, name='feature-categories'),
    path('api/v1/features/meta/navigation/', get_navigation, name='feature-navigation'),
    
    # API endpoints - Core
    path('api/v1/', include('apps.api.urls')),
    path('api/v1/rbac/', include('apps.rbac.urls')),
    
    # API endpoints - Features (Plugin Architecture)
    path('api/v1/pid/', include('apps.pid_analysis.urls')),
    path('api/v1/pfd/', include('apps.pfd_converter.urls')),
    path('api/v1/crs/', include('apps.crs_documents.urls')),
    path('api/v1/projects/', include('apps.core.project_urls')),
    # Add new feature URLs here - no routing changes needed!
    
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='swagger-ui'),  name='swagger-ui'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
