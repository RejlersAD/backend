"""
API URL routing.
Smart versioned API endpoints.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from apps.users.serializers_jwt import EmailTokenObtainPairSerializer
from .views import UserViewSet, HealthCheckView, CORSDiagnosticView
from .export_wrapper import pid_export_wrapper
from .email_views import verify_email, resend_verification_email, check_verification_status


# Custom JWT view for email-based login
class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer


# Create router for viewsets
router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')

urlpatterns = [
    # Health check
    path('health/', HealthCheckView.as_view(), name='health-check'),
    
    # CORS diagnostic endpoint
    path('cors-diagnostic/', CORSDiagnosticView.as_view(), name='cors-diagnostic'),
    
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
