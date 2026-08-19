from django.apps import AppConfig


class NonTeffMetadataConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.non_teff_metadata'
    verbose_name = 'Non-TEFF Metadata Extractor'
