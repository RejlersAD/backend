"""
CORS Diagnostic Views

Provides endpoints for testing CORS configuration without authentication.
"""

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from decouple import config
import json
import datetime


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


@csrf_exempt
@require_http_methods(["GET"])
def railway_health_check(request):
    """
    Railway deployment health check endpoint
    Simple endpoint for Railway to verify the application is running
    Returns 200 even if DB is not ready (for initial startup)
    """
    health_status = {
        'status': 'healthy',
        'service': 'radai-backend',
        'timestamp': datetime.datetime.now().isoformat(),
    }
    
    # Try database connection but don't fail if it's not ready
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_status['database'] = 'connected'
    except Exception as e:
        # Still return 200 but note DB issue
        health_status['database'] = 'initializing'
        health_status['db_note'] = str(e)[:100]
    
    return JsonResponse(health_status, status=200)


@csrf_exempt
@require_http_methods(["GET", "POST", "OPTIONS"])
def cors_health_check(request):
    """
    Comprehensive CORS health check endpoint
    Tests CORS functionality and provides diagnostic information
    """
    
    # Extract request information
    origin = request.META.get('HTTP_ORIGIN', 'NO_ORIGIN')
    user_agent = request.META.get('HTTP_USER_AGENT', 'NO_USER_AGENT')[:100]
    
    print(f"[CORS_HEALTH] === CORS HEALTH CHECK CALLED ===")
    print(f"[CORS_HEALTH] Method: {request.method}")
    print(f"[CORS_HEALTH] Origin: {origin}")
    print(f"[CORS_HEALTH] User-Agent: {user_agent}")
    print(f"[CORS_HEALTH] Path: {request.path}")
    
    # Create comprehensive health response
    health_data = {
        'status': 'success',
        'message': 'CORS is working correctly',
        'timestamp': datetime.datetime.now().isoformat(),
        'server_info': {
            'backend_url': config('BACKEND_URL', default='https://aiflowbackend-production.up.railway.app'),
            'frontend_url': config('FRONTEND_URL', default='https://airflow-frontend.vercel.app'),
            'cors_emergency_mode': config('CORS_EMERGENCY_MODE', default=True, cast=bool),
            'django_debug': config('DEBUG', default=False, cast=bool)
        },
        'request_info': {
            'method': request.method,
            'origin': origin,
            'user_agent': user_agent,
            'path': request.path,
            'remote_addr': request.META.get('REMOTE_ADDR', 'UNKNOWN'),
            'http_host': request.META.get('HTTP_HOST', 'UNKNOWN')
        },
        'cors_test': {
            'preflight_support': True,
            'credentials_support': True,
            'methods_allowed': ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
            'headers_allowed': ['Content-Type', 'Authorization', 'X-Requested-With']
        }
    }
    
    # Handle POST request with JSON data
    if request.method == 'POST':
        try:
            if request.content_type == 'application/json' and request.body:
                post_data = json.loads(request.body)
                health_data['received_data'] = post_data
                health_data['message'] = 'CORS POST test successful'
            else:
                health_data['message'] = 'CORS POST test successful (no JSON data)'
        except json.JSONDecodeError:
            health_data['message'] = 'CORS POST test successful (invalid JSON)'
    
    print(f"[CORS_HEALTH] Health check successful for {origin}")
    print(f"[CORS_HEALTH] Response: {health_data['message']}")
    
    # Return JSON response (CORS headers will be added by middleware)
    return JsonResponse(health_data, status=200)
