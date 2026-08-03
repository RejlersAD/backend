"""
ASGI config for RADAI project.
Configures Django Channels for WebSocket support
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialize Django ASGI application early to ensure the AppRegistry is populated
# before importing code that may import ORM models
django_asgi_app = get_asgi_application()

# Import WebSocket routing after Django is initialized
from apps.ml_detection.routing import websocket_urlpatterns as ml_detection_patterns
from apps.activity.routing import websocket_urlpatterns as activity_patterns
from apps.usage_tracking.routing import websocket_urlpatterns as usage_patterns

# Combine all WebSocket URL patterns
websocket_urlpatterns = ml_detection_patterns + activity_patterns + usage_patterns

# Application configuration
application = ProtocolTypeRouter({
    # HTTP requests are handled by Django's ASGI application
    "http": django_asgi_app,
    
    # WebSocket requests are handled by Channels
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
