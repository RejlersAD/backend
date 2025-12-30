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
        
        # Allow all origins (permanent solution)
        response['Access-Control-Allow-Origin'] = '*'
        
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
            response['Access-Control-Allow-Origin'] = '*'
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
