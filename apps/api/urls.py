"""
API URL routing.
Smart versioned API endpoints.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from rest_framework.response import Response
from rest_framework import status
from apps.users.serializers_jwt import EmailTokenObtainPairSerializer
from .views import UserViewSet, HealthCheckView, CORSDiagnosticView, dashboard_metrics, recent_activity, predictive_analytics
from .export_wrapper import pid_export_wrapper
from .email_views import verify_email, resend_verification_email, check_verification_status


# Custom JWT view for email-based login with error handling
class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    
    def post(self, request, *args, **kwargs):
        try:
            print(f"[LOGIN] Login attempt for email: {request.data.get('email')}")
            response = super().post(request, *args, **kwargs)
            print(f"[LOGIN] Login successful for: {request.data.get('email')}")
            return response
        except Exception as e:
            import traceback
            print(f"[LOGIN ERROR] Exception: {str(e)}")
            print(f"[LOGIN ERROR] Type: {type(e).__name__}")
            print(f"[LOGIN ERROR] Traceback:")
            traceback.print_exc()
            return Response(
                {'error': str(e), 'type': type(e).__name__},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# Create router for viewsets
router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    # Health check
    path('health/', HealthCheckView.as_view(), name='health-check'),
    
    # CORS diagnostic endpoint
    path('cors-diagnostic/', CORSDiagnosticView.as_view(), name='cors-diagnostic'),
    
    # Dashboard metrics (Advanced Intelligence)
    path('dashboard/metrics/', dashboard_metrics, name='dashboard-metrics'),
    
    # Real-time activity feed (Database & S3 History)
    path('activity/recent/', recent_activity, name='recent-activity'),
    
    # Predictive Analytics (ML-Powered Forecasting)
    path('analytics/predictions/', predictive_analytics, name='predictive-analytics'),
    
    # Authentication
    path('auth/login/', EmailTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Email Verification
    path('auth/verify-email/', verify_email, name='verify-email'),
    path('auth/resend-verification/', resend_verification_email, name='resend-verification'),
    path('auth/verification-status/', check_verification_status, name='verification-status'),
    
    # Core functionality (S3 storage)
    path('core/', include('apps.core.urls', namespace='core')),
    
    # Router URLs
    path('', include(router.urls)),
    # PID export wrapper (stable endpoint)
    path('pid-export/<int:pk>/', pid_export_wrapper, name='pid-export'),
]
