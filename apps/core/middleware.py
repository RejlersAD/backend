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
        # CRITICAL: Log that middleware is being called
        print(f"[CorsMiddleware] === MIDDLEWARE CALLED ===")
        print(f"[CorsMiddleware] Method: {request.method}")
        print(f"[CorsMiddleware] Path: {request.path}")
        print(f"[CorsMiddleware] Origin: {request.META.get('HTTP_ORIGIN', 'NO ORIGIN')}")
        
        # Get the origin from the request
        origin = request.META.get('HTTP_ORIGIN', '')
        
        # ULTRA-PERMISSIVE MODE: Allow ALL origins
        # If no origin header, use wildcard for maximum compatibility
        if not origin:
            origin = '*'
            print(f"[CorsMiddleware] No origin provided, using wildcard '*'")
        
        # Handle preflight OPTIONS request - MUST RESPOND IMMEDIATELY
        # This MUST happen BEFORE any other processing
        if request.method == 'OPTIONS':
            print(f"[CorsMiddleware] !!! OPTIONS PREFLIGHT REQUEST DETECTED !!!")
            print(f"[CorsMiddleware] Creating immediate 200 response with CORS headers...")
            
            response = HttpResponse(status=200)
            
            # Add ALL necessary CORS headers (ultra-permissive)
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Credentials'] = 'true' if origin != '*' else 'false'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
            response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override'
            response['Access-Control-Max-Age'] = '86400'  # Cache preflight for 24 hours
            response['Vary'] = 'Origin'
            
            print(f"[CorsMiddleware] ✓ OPTIONS preflight response prepared")
            print(f"[CorsMiddleware] ✓ Access-Control-Allow-Origin: {origin}")
            print(f"[CorsMiddleware] ✓ Status: 200")
            print(f"[CorsMiddleware] === RETURNING PREFLIGHT RESPONSE ===")
            
            return response
        
        # Process normal request
        print(f"[CorsMiddleware] Processing {request.method} request...")
        response = self.get_response(request)
        print(f"[CorsMiddleware] Response status: {response.status_code}")
        
        # ALWAYS add CORS headers to ALL responses
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Credentials'] = 'true' if origin != '*' else 'false'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
        response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override'
        response['Access-Control-Expose-Headers'] = 'Content-Type, Content-Disposition, X-CSRFToken'
        response['Vary'] = 'Origin'
        
        print(f"[CorsMiddleware] ✓ CORS headers added to response")
        print(f"[CorsMiddleware] === RETURNING RESPONSE ===")
        
        return response
