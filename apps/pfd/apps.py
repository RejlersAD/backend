"""
PFD App Configuration
"""
from django.apps import AppConfig


class PfdConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.pfd'
    verbose_name = 'PFD Project Management'
