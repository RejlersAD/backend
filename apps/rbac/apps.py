import logging
import os

from django.apps import AppConfig
from django.db.models.signals import post_migrate
logger = logging.getLogger(__name__)



def _sync_module_catalogue_after_migrate(sender, **kwargs):
    """Sync configured modules after schema setup, never during app loading."""
    if os.getenv('RBAC_AUTO_SYNC_MODULES', '1').strip().lower() in {'0', 'false', 'off'}:
        return
    from apps.core.test_runtime import is_test_runtime
    if is_test_runtime():
        return
    try:
        from apps.rbac.models import _sync_module_catalogue
        _sync_module_catalogue()
    except Exception:
        logger.exception('[RBAC] post-migrate module catalogue sync failed')


class RbacConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.rbac'
    verbose_name = 'Role-Based Access Control'

    def ready(self):
        """Register handlers only; do not inspect or query the database here."""
        import apps.rbac.signals  # noqa: F401
        import apps.rbac.ai_measurement_signals  # noqa: F401
        post_migrate.connect(
            _sync_module_catalogue_after_migrate,
            sender=self,
            dispatch_uid='rbac.sync_module_catalogue_after_migrate',
        )
