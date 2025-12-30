"""
Custom CORS Middleware - Permanent CORS Solution
Forces CORS headers on ALL responses regardless of django-cors-headers
"""
from django.utils.deprecation import MiddlewareMixin


class ForceCORSMiddleware(MiddlewareMixin):
    """
    FORCE CORS headers on all responses
    This is a nuclear option to ensure CORS always works
    """
    
    def process_response(self, request, response):
        """Add CORS headers to every response"""
        
        # Get origin from request or use default
        origin = request.headers.get('Origin', '*')
        
        # CRITICAL: When using credentials, we MUST return specific origin, not *
        # This is a CORS security requirement
        if origin and origin != '*':
            response['Access-Control-Allow-Origin'] = origin
        else:
            # Fallback to Vercel frontend for production
            response['Access-Control-Allow-Origin'] = 'https://airflow-frontend.vercel.app'
        
        # Allow credentials
        response['Access-Control-Allow-Credentials'] = 'true'
        
        # Allow all methods
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        
        # Allow all headers
        response['Access-Control-Allow-Headers'] = (
            'accept, accept-encoding, authorization, content-type, dnt, '
            'origin, user-agent, x-csrftoken, x-requested-with, x-custom-header, '
            'cache-control'
        )
        
        # Expose headers
        response['Access-Control-Expose-Headers'] = (
            'content-type, content-disposition, content-length'
        )
        
        # Cache preflight for 24 hours
        response['Access-Control-Max-Age'] = '86400'
        
        return response
    
    def process_request(self, request):
        """Handle OPTIONS preflight requests immediately"""
        if request.method == 'OPTIONS':
            from django.http import HttpResponse
            response = HttpResponse()
            
            # Get origin from request
            origin = request.headers.get('Origin', 'https://airflow-frontend.vercel.app')
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Credentials'] = 'true'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Access-Control-Allow-Headers'] = (
                'accept, accept-encoding, authorization, content-type, dnt, '
                'origin, user-agent, x-csrftoken, x-requested-with, x-custom-header, '
                'cache-control'
            )
            response['Access-Control-Max-Age'] = '86400'
            response.status_code = 200
            return response
        return None
