"""
Sales App Configuration
"""
from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.sales'
    verbose_name = 'Sales Management'
    
    def ready(self):
        """Initialize app - import signals if any"""
        pass
