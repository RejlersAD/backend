"""
Usage Tracking — WebSocket routing.
Merged into backend/config/asgi.py via `websocket_urlpatterns`.
"""
from django.urls import re_path

from . import consumers

# Soft-coded path — change here only.
USAGE_WS_PATH = r'ws/usage/$'

websocket_urlpatterns = [
    re_path(USAGE_WS_PATH, consumers.UsageStreamConsumer.as_asgi()),
]
