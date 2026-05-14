"""Spec Customization App Configuration."""
from django.apps import AppConfig


class SpecCustomizationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.spec_customization'
    verbose_name = 'Spec Customization (Paper Spec Extraction)'
