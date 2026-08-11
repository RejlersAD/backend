"""
ULTRA-SIMPLE CORS Middleware
Guaranteed to work without any imports that could fail
"""
import re
import time
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone

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


# Soft-coded: paths to skip (prefix match on request.path)
API_USAGE_LOG_SKIP_PREFIXES = (
    '/health/',
    '/static/',
    '/media/',
    '/admin/',
)


class ApiUsageLoggingMiddleware:
    """
    Logs every authenticated request to the `api_usage_logs` table
    (apps.core.models.ApiUsageLog). Mirrors the apps.usage_tracking
    middleware's pattern: request.user_id is NOT NULL on this table, so
    unauthenticated requests are skipped rather than logged with a
    placeholder id. Never raises — a failure here must not affect the
    actual API response.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)

        try:
            self._log(request, response, start)
        except Exception:
            pass  # Never let logging affect the response

        return response

    def _log(self, request, response, start):
        path = request.path

        for prefix in API_USAGE_LOG_SKIP_PREFIXES:
            if path.startswith(prefix):
                return

        # user_id is NOT NULL on api_usage_logs — nothing meaningful to log for anonymous requests
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return

        from .models import ApiUsageLog

        elapsed_ms = int((time.monotonic() - start) * 1000)

        ApiUsageLog.objects.create(
            endpoint=path[:255],
            method=request.method,
            user_id=request.user.id,
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            timestamp=timezone.now(),
        )
