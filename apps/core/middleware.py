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
    COMPREHENSIVE CORS middleware with emergency fallback protection
    This ALWAYS ensures CORS works regardless of other configurations
    Uses environment variables for complete soft-coded configuration
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Load comprehensive configuration
        self._load_cors_configuration()
        print(f"[CorsMiddleware] ✓ Initialized with EMERGENCY FALLBACK mode enabled")

    def _load_cors_configuration(self):
        """Load comprehensive CORS configuration with emergency fallback"""
        # EMERGENCY MODE: Enable ultra-permissive CORS for critical deployments
        self.emergency_mode = config('CORS_EMERGENCY_MODE', default=True, cast=bool)
        
        # Soft-coded comprehensive origins list
        self.core_origins = [
            config('FRONTEND_URL', default='https://airflow-frontend.vercel.app'),
            'https://airflow-frontend.vercel.app',
            'https://airflow-frontend-git-main-rejlers.vercel.app',
            'http://localhost:3000',
            'http://localhost:5173',
            'http://127.0.0.1:3000',
            'http://127.0.0.1:5173',
        ]
        
        # Get Railway environment URLs automatically
        railway_domain = config('RAILWAY_PUBLIC_DOMAIN', default='')
        if railway_domain:
            self.core_origins.append(f'https://{railway_domain}')
        
        # Get Vercel preview URLs
        vercel_url = config('VERCEL_URL', default='')
        if vercel_url:
            self.core_origins.append(f'https://{vercel_url}')
        
        # Load additional origins from environment
        additional = config('CORS_EXTRA_ORIGINS', default='', cast=str)
        if additional:
            extra_origins = [o.strip() for o in additional.split(',') if o.strip()]
            self.core_origins.extend(extra_origins)
        
        # Remove duplicates
        self.allowed_origins = list(dict.fromkeys(self.core_origins))
        
        # Enhanced pattern matching
        self.allow_all_vercel = config('CORS_ALLOW_ALL_VERCEL', default=True, cast=bool)
        self.allow_all_localhost = config('CORS_ALLOW_ALL_LOCALHOST', default=True, cast=bool)
        self.allow_railway = config('CORS_ALLOW_RAILWAY', default=True, cast=bool)
        
        print(f"[CorsMiddleware] === CONFIGURATION LOADED ===")
        print(f"[CorsMiddleware] Emergency Mode: {self.emergency_mode}")
        print(f"[CorsMiddleware] Configured Origins: {self.allowed_origins}")
        print(f"[CorsMiddleware] Allow Vercel Pattern: {self.allow_all_vercel}")
        print(f"[CorsMiddleware] Allow Localhost Pattern: {self.allow_all_localhost}")
        print(f"[CorsMiddleware] Allow Railway Pattern: {self.allow_railway}")
        print(f"[CorsMiddleware] === READY FOR REQUESTS ===")

    def _is_origin_allowed(self, origin):
        """
        Enhanced origin validation with emergency fallback
        ALWAYS allows origins in emergency mode for maximum reliability
        """
        if not origin:
            return self.emergency_mode  # Allow if emergency mode
        
        # EMERGENCY MODE: Allow everything
        if self.emergency_mode:
            print(f"[CorsMiddleware] EMERGENCY MODE: Allowing origin {origin}")
            return True
        
        # Check exact matches first
        if origin in self.allowed_origins:
            return True
        
        # Check pattern matches
        patterns_to_check = [
            (self.allow_all_vercel, '.vercel.app'),
            (self.allow_all_localhost, 'localhost'),
            (self.allow_all_localhost, '127.0.0.1'),
            (self.allow_railway, '.railway.app'),
            (self.allow_railway, '.up.railway.app'),
        ]
        
        for is_enabled, pattern in patterns_to_check:
            if is_enabled and pattern in origin:
                print(f"[CorsMiddleware] Pattern match: {origin} matches {pattern}")
                return True
        
        print(f"[CorsMiddleware] Origin NOT allowed: {origin}")
        return False

    def __call__(self, request):
        # COMPREHENSIVE REQUEST LOGGING
        print(f"[CorsMiddleware] ========================================")
        print(f"[CorsMiddleware] 🚀 INCOMING REQUEST")
        print(f"[CorsMiddleware] Method: {request.method}")
        print(f"[CorsMiddleware] Path: {request.path}")
        print(f"[CorsMiddleware] Full URL: {request.build_absolute_uri()}")
        print(f"[CorsMiddleware] Origin: {request.META.get('HTTP_ORIGIN', 'NO ORIGIN')}")
        print(f"[CorsMiddleware] User-Agent: {request.META.get('HTTP_USER_AGENT', 'NO USER AGENT')[:100]}")
        print(f"[CorsMiddleware] Referer: {request.META.get('HTTP_REFERER', 'NO REFERER')}")
        print(f"[CorsMiddleware] Emergency Mode: {self.emergency_mode}")
        
        # Extract and validate origin
        origin = request.META.get('HTTP_ORIGIN', '')
        
        # Determine response origin header
        if self.emergency_mode:
            # EMERGENCY: Always use the requesting origin or wildcard
            response_origin = origin if origin else '*'
            print(f"[CorsMiddleware] 🆘 EMERGENCY MODE: Using origin {response_origin}")
        elif origin and self._is_origin_allowed(origin):
            response_origin = origin
            print(f"[CorsMiddleware] ✅ Origin validated: {origin}")
        else:
            # Fallback to primary frontend URL or wildcard
            response_origin = config('FRONTEND_URL', default='https://airflow-frontend.vercel.app')
            print(f"[CorsMiddleware] ⚠️ Using fallback origin: {response_origin}")
        
        # HANDLE PREFLIGHT (OPTIONS) - CRITICAL FOR CORS
        if request.method == 'OPTIONS':
            print(f"[CorsMiddleware] 🎯 PREFLIGHT OPTIONS REQUEST DETECTED")
            print(f"[CorsMiddleware] Creating CORS preflight response...")
            
            # Create immediate preflight response
            response = HttpResponse(
                status=200,
                content='CORS Preflight OK',
                content_type='text/plain'
            )
            
            # Add COMPREHENSIVE CORS headers for preflight
            self._add_cors_headers(response, response_origin, is_preflight=True)
            
            print(f"[CorsMiddleware] ✅ PREFLIGHT RESPONSE READY")
            print(f"[CorsMiddleware] Status: 200")
            print(f"[CorsMiddleware] Headers: {dict(response.items())}")
            print(f"[CorsMiddleware] ========================================")
            
            return response
        
        # PROCESS NORMAL REQUEST
        print(f"[CorsMiddleware] 📋 Processing normal {request.method} request...")
        
        try:
            response = self.get_response(request)
            print(f"[CorsMiddleware] ✅ Request processed successfully")
            print(f"[CorsMiddleware] Response status: {response.status_code}")
        except Exception as e:
            print(f"[CorsMiddleware] ❌ Request processing failed: {str(e)}")
            # Create error response with CORS headers
            response = HttpResponse(
                status=500,
                content='Internal Server Error',
                content_type='text/plain'
            )
        
        # ALWAYS add CORS headers to response
        self._add_cors_headers(response, response_origin, is_preflight=False)
        
        print(f"[CorsMiddleware] ✅ CORS headers applied to response")
        print(f"[CorsMiddleware] Final status: {response.status_code}")
        print(f"[CorsMiddleware] ========================================")
        
        return response
    
    def _add_cors_headers(self, response, origin, is_preflight=False):
        """Add comprehensive CORS headers to any response"""
        # Core CORS headers
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Credentials'] = 'true' if origin != '*' else 'false'
        response['Vary'] = 'Origin'
        
        # Methods and Headers (always include for reliability)
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
        response['Access-Control-Allow-Headers'] = (
            'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, '
            'User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override, '
            'Cache-Control, Pragma, X-Custom-Header'
        )
        
        # Expose headers for JavaScript access
        response['Access-Control-Expose-Headers'] = (
            'Content-Type, Content-Disposition, Content-Length, X-CSRFToken, '
            'Cache-Control, Pragma, Expires'
        )
        
        # Preflight-specific headers
        if is_preflight:
            response['Access-Control-Max-Age'] = config('CORS_PREFLIGHT_MAX_AGE', default='86400', cast=str)
        
        print(f"[CorsMiddleware] 📝 CORS headers added for origin: {origin}")
