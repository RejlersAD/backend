"""
CORS Diagnostic Views

Provides endpoints for testing CORS configuration without authentication.
"""

from django.http import JsonResponse
from django.views import View


class CorsTestView(View):
    """
    Simple view to test CORS configuration.
    
    GET /api/v1/cors-test/
    OPTIONS /api/v1/cors-test/
    
    Returns basic info about the request to verify CORS headers are working.
    """
    
    def options(self, request, *args, **kwargs):
        """Handle OPTIONS preflight request"""
        return JsonResponse({
            'message': 'CORS OPTIONS preflight successful',
            'method': 'OPTIONS',
            'path': request.path,
            'origin': request.META.get('HTTP_ORIGIN', 'No origin header'),
        })
    
    def get(self, request, *args, **kwargs):
        """Handle GET request"""
        return JsonResponse({
            'message': 'CORS test successful',
            'method': 'GET',
            'path': request.path,
            'origin': request.META.get('HTTP_ORIGIN', 'No origin header'),
            'authenticated': request.user.is_authenticated,
            'user': str(request.user) if request.user.is_authenticated else 'Anonymous',
        })
    
    def post(self, request, *args, **kwargs):
        """Handle POST request"""
        return JsonResponse({
            'message': 'CORS POST test successful',
            'method': 'POST',
            'path': request.path,
            'origin': request.META.get('HTTP_ORIGIN', 'No origin header'),
            'content_type': request.content_type,
        })
