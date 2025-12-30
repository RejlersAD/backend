"""
Ultra-simple health check WSGI application
This is independent of Django and starts immediately
"""

def simple_health_check(environ, start_response):
    """
    Simplest possible health check - just returns 200 OK
    Works even if Django hasn't fully initialized
    """
    path = environ.get('PATH_INFO', '')
    
    if path == '/health/' or path == '/api/v1/health/':
        status = '200 OK'
        headers = [
            ('Content-Type', 'application/json'),
            ('Content-Length', '27')
        ]
        start_response(status, headers)
        return [b'{"status":"healthy","ok":true}']
    
    # For all other paths, let Django handle it
    return None


def application(environ, start_response):
    """
    Combined WSGI application
    Try simple health check first, then fall back to Django
    """
    # Try simple health check first
    health_response = simple_health_check(environ, start_response)
    if health_response is not None:
        return health_response
    
    # Fall back to Django application
    from config.wsgi import application as django_app
    return django_app(environ, start_response)
