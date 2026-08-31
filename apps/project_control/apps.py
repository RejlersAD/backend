from django.apps import AppConfig


class ProjectControlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.project_control'
    label = 'project_control'
    verbose_name = 'Project Management (Phased)'

    def ready(self):
        from django.apps import apps
        if apps.is_installed('apps.procurement') and apps.is_installed('apps.finance'):
            from . import signals  # noqa: F401
