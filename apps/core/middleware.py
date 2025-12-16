"""
Custom Middleware for AIFlow
Ensures CORS headers are always present
Uses soft-coded configuration via environment variables
"""
from django.http import HttpResponse
from decouple import config
import os


class CorsMiddleware:
    """
    Custom CORS middleware to ensure headers are always added
    This is a fallback to ensure CORS works even if django-cors-headers fails
    Uses environment variables for soft-coded configuration
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Load allowed origins from environment or use defaults
        self._load_allowed_origins()

    def _load_allowed_origins(self):
        """Load allowed origins from environment variable with defaults"""
        # Soft-coded default origins - can be overridden by environment variables
        default_origins = [
            config('FRONTEND_URL', default='https://airflow-frontend.vercel.app'),
            'http://localhost:3000',
            'http://localhost:5173',
            'http://127.0.0.1:3000',
            'http://127.0.0.1:5173',
        ]
        
        # Get additional origins from environment (comma-separated)
        env_origins = config('CORS_ALLOWED_ORIGINS', default='', cast=str)
        if env_origins:
            additional_origins = [origin.strip() for origin in env_origins.split(',') if origin.strip()]
            default_origins.extend(additional_origins)
        
        # Remove duplicates while preserving order
        seen = set()
        self.allowed_origins = []
        for origin in default_origins:
            if origin and origin not in seen:
                seen.add(origin)
                self.allowed_origins.append(origin)
        
        # Store wildcard patterns for regex matching
        self.allow_vercel = config('CORS_ALLOW_VERCEL', default=True, cast=bool)
        self.allow_localhost = config('CORS_ALLOW_LOCALHOST', default=True, cast=bool)
        
        print(f"[CorsMiddleware] Loaded allowed origins: {self.allowed_origins}")
        print(f"[CorsMiddleware] Allow Vercel (*.vercel.app): {self.allow_vercel}")
        print(f"[CorsMiddleware] Allow Localhost: {self.allow_localhost}")

    def _is_origin_allowed(self, origin):
        """
        Check if origin is allowed using soft-coded rules
        Returns True if origin should be allowed
        """
        if not origin:
            return False
        
        # Check exact matches
        if origin in self.allowed_origins:
            return True
        
        # Check Vercel pattern if enabled
        if self.allow_vercel and '.vercel.app' in origin:
            return True
        
        # Check localhost pattern if enabled
        if self.allow_localhost and ('localhost' in origin or '127.0.0.1' in origin):
            return True
        
        return False

    def __call__(self, request):
        # Get the origin from the request
        origin = request.META.get('HTTP_ORIGIN', '')
        
        # Check if origin is allowed
        is_allowed = self._is_origin_allowed(origin)
        
        # Handle preflight OPTIONS request - MUST RESPOND IMMEDIATELY
        if request.method == 'OPTIONS':
            response = HttpResponse(status=200)
            if origin and is_allowed:
                response['Access-Control-Allow-Origin'] = origin
                response['Access-Control-Allow-Credentials'] = 'true'
                response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
                response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override'
                response['Access-Control-Max-Age'] = '86400'
                response['Vary'] = 'Origin'
                print(f"[CorsMiddleware] ✓ OPTIONS request from {origin} to {request.path} - ALLOWED")
            else:
                # STILL ALLOW but log as warning - be permissive for debugging
                response['Access-Control-Allow-Origin'] = origin if origin else '*'
                response['Access-Control-Allow-Credentials'] = 'true'
                response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
                response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override'
                response['Access-Control-Max-Age'] = '86400'
                response['Vary'] = 'Origin'
                print(f"[CorsMiddleware] ⚠️  OPTIONS request from {origin} to {request.path} - ALLOWED (permissive mode)")
            return response
        
        # Process the request
        response = self.get_response(request)
        
        # Add CORS headers to ALL responses regardless of status code
        if origin:
            # Always add CORS headers if there's an origin
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Credentials'] = 'true'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
            response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override'
            response['Access-Control-Expose-Headers'] = 'Content-Type, Content-Disposition, X-CSRFToken'
            response['Vary'] = 'Origin'
            
            if is_allowed:
                print(f"[CorsMiddleware] ✓ {request.method} {request.path} from {origin} - ALLOWED (Status: {response.status_code})")
            else:
                print(f"[CorsMiddleware] ⚠️  {request.method} {request.path} from {origin} - ALLOWED (permissive mode, Status: {response.status_code})")
        
        return response
