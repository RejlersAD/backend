"""
URL configuration for AIFlow project.
Smart routing with versioned API endpoints.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.core.cors_test_views import CorsTestView, cors_health_check

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # CORS diagnostic endpoints (no auth required)
    path('api/v1/cors-test/', CorsTestView.as_view(), name='cors-test'),
    path('api/v1/cors/health/', cors_health_check, name='cors-health'),
    
    # API endpoints
    path('api/v1/', include('apps.api.urls')),
    path('api/v1/pid/', include('apps.pid_analysis.urls')),
    path('api/v1/pfd/', include('apps.pfd_converter.urls')),
    path('api/v1/rbac/', include('apps.rbac.urls')),
    
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
