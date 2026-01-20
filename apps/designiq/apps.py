"""
DesignIQ App Configuration
"""
from django.apps import AppConfig


class DesignIQConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.designiq'
    verbose_name = 'DesignIQ - Engineering Design Intelligence'
    
    def ready(self):
        """Import signals when app is ready"""
        try:
            import apps.designiq.signals  # noqa
        except ImportError:
            pass
