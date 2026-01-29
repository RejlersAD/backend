"""
Process Datasheet App Configuration
"""
from django.apps import AppConfig


class ProcessDatasheetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.process_datasheet'
    verbose_name = 'Process Datasheet'

    def ready(self):
        """Import signals when app is ready"""
        try:
            import apps.process_datasheet.signals  # noqa
        except ImportError:
            pass
