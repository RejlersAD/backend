"""
Finance app configuration
"""
from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.finance'
    verbose_name = 'Finance Invoice Automation'
    
    def ready(self):
        """Import signals when app is ready"""
        pass
