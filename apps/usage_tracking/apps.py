from django.apps import AppConfig


class UsageTrackingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.usage_tracking'
    verbose_name = 'Usage Tracking & Metering'
    
    def ready(self):
        """Import signal handlers and setup background tasks"""
        try:
            import apps.usage_tracking.signals  # noqa
        except ImportError:
            pass
