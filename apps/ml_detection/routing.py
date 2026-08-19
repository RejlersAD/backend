"""
WebSocket routing for ML Detection
"""
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/detection/$', consumers.RealTimeDetectionConsumer.as_asgi()),
    re_path(r'ws/metrics/$', consumers.SystemMetricsConsumer.as_asgi()),
]
