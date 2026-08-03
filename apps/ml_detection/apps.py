"""
Django App Configuration for ML Detection
"""
from django.apps import AppConfig


class MlDetectionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.ml_detection'
    verbose_name = 'ML Detection and Alerts'
    
    def ready(self):
        """Initialize app when Django starts"""
        import apps.ml_detection.signals
