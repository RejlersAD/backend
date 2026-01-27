"""
Django App Configuration for Real-Time Activity Tracking
"""
from django.apps import AppConfig


class ActivityConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.activity'
    verbose_name = 'Real-Time Activity Tracking'
    
    def ready(self):
        """Initialize app when Django starts"""
        pass  # No signals needed yet
