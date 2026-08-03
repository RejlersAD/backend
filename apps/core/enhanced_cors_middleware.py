"""
Enhanced CORS middleware for handling file uploads and complex requests
"""
from django.utils.deprecation import MiddlewareMixin
from django.http import HttpResponse
import re

class EnhancedCORSMiddleware(MiddlewareMixin):
    """Enhanced CORS middleware specifically for upload endpoints"""
    
    UPLOAD_PATHS = [
        r'/api/v1/pid/drawings/upload/',
        r'/api/v1/pid/reference-documents/upload/',
    ]
    
    def process_request(self, request):
        """Handle CORS preflight requests for upload endpoints"""
        if request.method == 'OPTIONS':
            # Check if this is an upload endpoint
            path = request.get_full_path()
            if any(re.match(pattern, path) for pattern in self.UPLOAD_PATHS):
                response = HttpResponse()
                response['Access-Control-Allow-Origin'] = self.get_allowed_origin(request)
                response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
                response['Access-Control-Allow-Headers'] = ', '.join([
                    'Accept',
                    'Accept-Encoding',
                    'Authorization',
                    'Content-Type',
                    'DNT',
                    'Origin',
                    'User-Agent',
                    'X-CSRFToken',
                    'X-Requested-With',
                ])
                response['Access-Control-Allow-Credentials'] = 'true'
                response['Access-Control-Max-Age'] = '86400'  # 24 hours
                return response
        return None
    
    def process_response(self, request, response):
        """Add CORS headers to actual responses"""
        path = request.get_full_path()
        if any(re.match(pattern, path) for pattern in self.UPLOAD_PATHS):
            response['Access-Control-Allow-Origin'] = self.get_allowed_origin(request)
            response['Access-Control-Allow-Credentials'] = 'true'
            response['Access-Control-Expose-Headers'] = 'Content-Disposition, Content-Length'
        return response
    
    def get_allowed_origin(self, request):
        """Get appropriate allowed origin based on request"""
        origin = request.META.get('HTTP_ORIGIN', '')
        
        # Allowed origins
        allowed_origins = [
            'https://airflow-frontend.vercel.app',
            'http://localhost:3000',
            'http://127.0.0.1:3000',
            'http://localhost:5173',
            'http://127.0.0.1:5173',
        ]
        
        # Check for Vercel preview deployments
        if origin.endswith('.vercel.app'):
            return origin
            
        # Check localhost with any port
        if origin.startswith('http://localhost:') or origin.startswith('http://127.0.0.1:'):
            return origin
            
        # Check allowed origins
        if origin in allowed_origins:
            return origin
            
        # Default fallback
        return 'https://airflow-frontend.vercel.app'