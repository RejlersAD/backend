from django.apps import AppConfig


class PfdConverterConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.pfd_converter'
    verbose_name = 'PFD to P&ID Converter'
