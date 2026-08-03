"""
ULTRA-SIMPLE CORS Middleware
Guaranteed to work without any imports that could fail
"""
import re
from django.conf import settings
from django.http import HttpResponse

# RFC 1034/1035: underscores are not valid in hostnames.
# Docker container names (e.g. backend_local, aiflow_backend_local) contain underscores,
# so Django's DisallowedHost raises a 400 for any request whose Host header is the raw
# Docker service name.  We normalise it to 'localhost' before CommonMiddleware runs.
_DOCKER_HOST_RE = re.compile(r'[a-z0-9_-]+_[a-z0-9_-]+(:\d+)?$', re.IGNORECASE)


class NormaliseDockerHostMiddleware:
    """
    Development-only middleware.
    Replaces Docker-internal hostnames that contain underscores (invalid per RFC 1034/1035)
    with 'localhost' so Django's host-header validation never raises DisallowedHost for
    internal health-check or container-to-container traffic.

    Position: must appear BEFORE django.middleware.common.CommonMiddleware in MIDDLEWARE.
    Activated automatically when DEBUG=True or ENVIRONMENT in ('local','development','test').
    """

    def __init__(self, get_response):
        self.get_response = get_response
        active_envs = {'local', 'development', 'test'}
        env = getattr(settings, 'AIFLOW_ENVIRONMENT', '') or ''
        self._active = settings.DEBUG or env.lower() in active_envs

    def __call__(self, request):
        if self._active:
            host = request.META.get('HTTP_HOST', '')
            domain = host.split(':')[0]
            # Normalise only if the domain itself contains underscores
            # (which makes it RFC-invalid and triggers DisallowedHost)
            if '_' in domain:
                port = host.split(':', 1)[1] if ':' in host else '8000'
                request.META['HTTP_HOST'] = f'localhost:{port}'
        return self.get_response(request)


class CorsMiddleware:
    """Ultra-simple CORS middleware with zero dependencies"""
    
    def __init__(self, get_response):
        self.get_response = get_response
        print("[CorsMiddleware] Ultra-simple CORS initialized")

    def __call__(self, request):
        print(f"[CorsMiddleware] {request.method} {request.path}")
        
        # Get origin - use safe default
        origin = request.META.get('HTTP_ORIGIN', 'https://airflow-frontend.vercel.app')
        print(f"[CorsMiddleware] Origin: {origin}")
        
        # Handle OPTIONS immediately
        if request.method == 'OPTIONS':
            print("[CorsMiddleware] Handling OPTIONS preflight")
            response = HttpResponse('')
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET,POST,PUT,PATCH,DELETE,OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,X-Requested-With'
            response['Access-Control-Max-Age'] = '86400'
            return response
        
        # Process request
        response = self.get_response(request)
        
        # Add CORS headers to all responses
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET,POST,PUT,PATCH,DELETE,OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,X-Requested-With'
        response['Access-Control-Expose-Headers'] = 'Content-Type'
        
        print("[CorsMiddleware] CORS headers added")
        return response
