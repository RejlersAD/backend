"""
CORS Health Check View
Always returns 200 with CORS headers - for testing CORS configuration
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


@csrf_exempt
@require_http_methods(["GET", "OPTIONS"])
def cors_health_check(request):
    """
    CORS-enabled health check endpoint
    Returns 200 with full CORS headers for testing
    """
    response = JsonResponse({
        'status': 'healthy',
        'cors': 'enabled',
        'message': 'CORS is working correctly',
        'allowed_origins': '*',
        'timestamp': str(request.META.get('HTTP_DATE', ''))
    })
    
    # Force CORS headers
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'accept, authorization, content-type, origin, user-agent'
    response['Access-Control-Allow-Credentials'] = 'true'
    response['Access-Control-Max-Age'] = '86400'
    
    return response
