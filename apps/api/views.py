"""
API views for AIFlow.
Smart ViewSets with proper permissions and pagination.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from django.contrib.auth import get_user_model
from django.conf import settings
from apps.users.serializers import UserSerializer, UserRegistrationSerializer
import os

User = get_user_model()


class HealthCheckView(APIView):
    """
    Health check endpoint to verify API is running.
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Return health status."""
        return Response({
            'status': 'healthy',
            'message': 'AIFlow API is running successfully'
        })


class CORSDiagnosticView(APIView):
    """
    CORS diagnostic endpoint to debug CORS configuration.
    Returns current CORS settings and request origin.
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Return CORS diagnostic information."""
        origin = request.META.get('HTTP_ORIGIN', 'No origin header')
        
        return Response({
            'status': 'cors_diagnostic',
            'request_origin': origin,
            'cors_settings': {
                'allowed_origins': list(settings.CORS_ALLOWED_ORIGINS) if hasattr(settings, 'CORS_ALLOWED_ORIGINS') else [],
                'allow_credentials': getattr(settings, 'CORS_ALLOW_CREDENTIALS', False),
                'allow_all_origins': getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False),
            },
            'environment_variables': {
                'FRONTEND_URL': os.getenv('FRONTEND_URL', 'Not set - using default: https://airflow-frontend.vercel.app'),
                'BACKEND_URL': os.getenv('BACKEND_URL', 'Not set'),
                'CORS_ALLOW_VERCEL': os.getenv('CORS_ALLOW_VERCEL', 'Not set - using default: true'),
                'CORS_ALLOW_LOCALHOST': os.getenv('CORS_ALLOW_LOCALHOST', 'Not set - using default: true'),
            },
            'request_headers': {
                'Origin': request.META.get('HTTP_ORIGIN', 'Not present'),
                'Host': request.META.get('HTTP_HOST', 'Not present'),
                'User-Agent': request.META.get('HTTP_USER_AGENT', 'Not present'),
            },
            'message': 'If FRONTEND_URL is "Not set", add it in Railway dashboard: Variables tab'
        })
    
    def options(self, request):
        """Handle OPTIONS preflight for diagnostic endpoint."""
        return Response({'status': 'preflight_ok'}, status=200)


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for user operations.
    Smart CRUD operations with custom actions.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    
    def get_permissions(self):
        """Set permissions based on action."""
        if self.action == 'create':
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'create':
            return UserRegistrationSerializer
        return UserSerializer
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Get current user information."""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
    @action(detail=False, methods=['put', 'patch'], permission_classes=[IsAuthenticated])
    def update_profile(self, request):
        """Update current user's profile."""
        serializer = self.get_serializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
