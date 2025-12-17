"""
BULLETPROOF CORS Middleware for AIFlow
Ensures CORS headers are ALWAYS present with zero configuration
Emergency fallback mode for critical deployments
"""
from django.http import HttpResponse
from decouple import config
import os


class CorsMiddleware:
    """
    BULLETPROOF CORS middleware - ALWAYS works
    Ultra-simple implementation with emergency mode enabled by default
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        print("[CorsMiddleware] BULLETPROOF CORS middleware initialized")

    def __call__(self, request):
        print(f"[CorsMiddleware] PROCESSING: {request.method} {request.path}")
        
        # Get origin from request
        origin = request.META.get('HTTP_ORIGIN', '')
        print(f"[CorsMiddleware] Origin: {origin}")
        
        # EMERGENCY MODE: Allow ALL origins
        allowed_origin = origin if origin else 'https://airflow-frontend.vercel.app'
        
        # Handle OPTIONS preflight IMMEDIATELY
        if request.method == 'OPTIONS':
            print("[CorsMiddleware] PREFLIGHT OPTIONS - Creating immediate response")
            
            response = HttpResponse(
                status=200,
                content='CORS Preflight OK',
                content_type='text/plain'
            )
            
            # Add ALL required CORS headers
            response['Access-Control-Allow-Origin'] = allowed_origin
            response['Access-Control-Allow-Credentials'] = 'false'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
            response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override, Cache-Control, Pragma'
            response['Access-Control-Max-Age'] = '86400'
            response['Vary'] = 'Origin'
            
            print("[CorsMiddleware] PREFLIGHT RESPONSE READY")
            return response
        
        # Process normal request
        print("[CorsMiddleware] Processing normal request...")
        
        try:
            response = self.get_response(request)
        except Exception as e:
            print(f"[CorsMiddleware] ERROR in request processing: {e}")
            response = HttpResponse(
                status=500,
                content='Internal Server Error',
                content_type='text/plain'
            )
        
        # ALWAYS add CORS headers to response
        response['Access-Control-Allow-Origin'] = allowed_origin
        response['Access-Control-Allow-Credentials'] = 'false'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
        response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override, Cache-Control, Pragma'
        response['Access-Control-Expose-Headers'] = 'Content-Type, Content-Disposition, Content-Length'
        response['Vary'] = 'Origin'
        
        print("[CorsMiddleware] CORS headers added to response")
        return response
