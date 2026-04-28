from django.apps import AppConfig


class UsageTrackingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.usage_tracking'
    verbose_name = 'Usage Tracking'

    def ready(self):
        # Wire up realtime broadcast signal — safe to import lazily here
        # because Django guarantees ready() runs after app registry load.
        try:
            from . import signals  # noqa: F401
        except Exception:
            # Realtime layer is optional — never block startup if it
            # fails (e.g. channels not installed in a slim image).
            pass
