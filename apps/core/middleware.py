"""
ULTRA-SIMPLE CORS Middleware
Guaranteed to work without any imports that could fail
"""
from django.http import HttpResponse


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
