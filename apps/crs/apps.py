from django.apps import AppConfig


class CrsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.crs'
    verbose_name = 'Comment Resolution Sheet'

    def import_models(self):
        super().import_models()
        # Register split models for management commands as well as HTTP routes.
        from . import revision_models  # noqa: F401
