"""
EMERGENCY CORS Decorators
Triple redundancy for CORS headers
"""
from functools import wraps
from django.http import HttpResponse


def emergency_cors(view_func):
    """
    Emergency CORS decorator
    Adds CORS headers directly to view responses
    Triple redundancy for critical API endpoints
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # Handle OPTIONS preflight
        if request.method == 'OPTIONS':
            response = HttpResponse(status=200)
        else:
            response = view_func(request, *args, **kwargs)
        
        # Always add CORS headers
        origin = request.META.get('HTTP_ORIGIN', 'https://airflow-frontend.vercel.app')
        
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Credentials'] = 'false'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD'
        response['Access-Control-Allow-Headers'] = 'Accept, Accept-Encoding, Authorization, Content-Type, DNT, Origin, User-Agent, X-CSRFToken, X-Requested-With, X-HTTP-Method-Override'
        response['Access-Control-Max-Age'] = '86400'
        response['Vary'] = 'Origin'
        
        return response
    return wrapper