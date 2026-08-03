"""
MLflow Integration URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.mlflow_integration.views import MLflowViewSet

router = DefaultRouter()
router.register(r'tracking', MLflowViewSet, basename='mlflow')

app_name = 'mlflow_integration'

urlpatterns = [
    path('', include(router.urls)),
]
