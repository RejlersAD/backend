from django.apps import AppConfig


class RbacConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.rbac'
    verbose_name = 'Role-Based Access Control'
    
    def ready(self):
        """Import signals when app is ready"""
        import apps.rbac.signals
