from django.apps import AppConfig


class RbacConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.rbac'
    verbose_name = 'Role-Based Access Control'

    # Soft-coded: auto-synchronise the module catalogue into the DB on startup
    # so ANY new feature added to ALL_MODULES_CATALOGUE appears under
    # Role & Permission without a manual seed step. Disable via env var.
    AUTO_SYNC_MODULE_CATALOGUE = True

    def ready(self):
        """Import signals when app is ready"""
        import apps.rbac.signals
        import apps.rbac.ai_measurement_signals  # noqa: F401
        self._maybe_sync_module_catalogue()

    def _maybe_sync_module_catalogue(self):
        import os
        if os.getenv('RBAC_AUTO_SYNC_MODULES', '1').strip().lower() in {'0', 'false', 'off'}:
            return
        if not self.AUTO_SYNC_MODULE_CATALOGUE:
            return
        try:
            from django.db import connection
            from django.db.utils import OperationalError, ProgrammingError
            if 'rbac_modules' not in set(connection.introspection.table_names()):
                return  # pre-migration (fresh DB) — seed_rbac handles it
            from apps.rbac.models import _sync_module_catalogue
            _sync_module_catalogue()
        except (OperationalError, ProgrammingError):
            return  # DB not reachable yet (e.g. during collectstatic/migrate)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('[RBAC] module catalogue auto-sync failed')
