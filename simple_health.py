"""
Ultra-simple health check WSGI application
This is independent of Django and starts immediately
"""

import os

# Set Django settings module before any Django imports
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Import Django app at module level to avoid repeated imports
_django_app = None


def get_django_app():
    """Lazy load Django application"""
    global _django_app
    if _django_app is None:
        from django.core.wsgi import get_wsgi_application
        _django_app = get_wsgi_application()
    return _django_app


def application(environ, start_response):
    """
    Combined WSGI application with fast health check
    """
    path = environ.get('PATH_INFO', '')
    
    # Fast path for health checks - no Django initialization needed
    if path in ['/health/', '/api/v1/health/']:
        status = '200 OK'
        response_body = b'{"status":"healthy","ok":true}'
        headers = [
            ('Content-Type', 'application/json'),
            ('Content-Length', str(len(response_body)))
        ]
        start_response(status, headers)
        return [response_body]
    
    # All other paths go through Django
    django_app = get_django_app()
    return django_app(environ, start_response)
