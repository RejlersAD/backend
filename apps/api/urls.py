"""
API URL routing.
Smart versioned API endpoints.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError, AuthenticationFailed
from apps.users.serializers_jwt import EmailTokenObtainPairSerializer
from .views import UserViewSet, HealthCheckView, CORSDiagnosticView, dashboard_metrics, recent_activity, predictive_analytics, projects_stats, pid_stats, usage_daily
from .export_wrapper import pid_export_wrapper
from .email_views import verify_email, resend_verification_email, check_verification_status


# Custom JWT view for email-based login with comprehensive error handling
class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    
    def post(self, request, *args, **kwargs):
        try:
            print(f"[LOGIN] Login attempt for email: {request.data.get('email')}")
            response = super().post(request, *args, **kwargs)
            print(f"[LOGIN] Login successful for: {request.data.get('email')}")
            return response
        except (ValidationError, AuthenticationFailed) as e:
            # Let DRF handle proper 400/401 responses for auth failures
            print(f"[LOGIN] Authentication failed for {request.data.get('email')}: {str(e)}")
            raise
        except Exception as e:
            # Catch any unexpected errors and log them comprehensively
            import traceback
            import sys
            
            error_details = {
                'exception': str(e),
                'type': type(e).__name__,
                'email': request.data.get('email', 'N/A'),
            }
            
            print(f"[LOGIN ERROR] Unexpected exception during login:")
            print(f"[LOGIN ERROR]   Email: {error_details['email']}")
            print(f"[LOGIN ERROR]   Exception Type: {error_details['type']}")
            print(f"[LOGIN ERROR]   Exception Message: {error_details['exception']}")
            print(f"[LOGIN ERROR]   Traceback:")
            traceback.print_exc(file=sys.stdout)
            
            # Log to Django logger as well
            logger = __import__('logging').getLogger(__name__)
            logger.error(f"Login error for {error_details['email']}: {error_details}", exc_info=True)
            
            # Return a user-friendly error message
            return Response(
                {
                    'detail': 'An unexpected error occurred during login. Please try again.',
                    'error_type': error_details['type'],
                    'debug_info': error_details['exception'] if __import__('django.conf').settings.DEBUG else None
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# Create router for viewsets
router = DefaultRouter()
# Changed from 'users' to 'user-management' to avoid conflict with apps.users.urls
router.register(r'user-management', UserViewSet, basename='user')

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
    
    # SOFT-CODED: Stats endpoints for dashboard integration
    path('projects/stats/', projects_stats, name='projects-stats'),
    path('pid/stats/', pid_stats, name='pid-stats'),

    # Usage analytics (daily trend from usage_log table)
    path('dashboard/usage/', usage_daily, name='dashboard-usage'),
    
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
