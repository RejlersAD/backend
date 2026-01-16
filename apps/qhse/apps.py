# QHSE App Configuration
from django.apps import AppConfig


class QhseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.qhse'
    verbose_name = 'QHSE Management'
    
    def ready(self):
        """Import signals when app is ready"""
        try:
            import apps.qhse.signals  # noqa
        except ImportError:
            pass
