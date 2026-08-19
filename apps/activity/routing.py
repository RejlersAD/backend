"""
WebSocket routing for Activity Stream
"""
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/activity/$', consumers.ActivityStreamConsumer.as_asgi()),
]
