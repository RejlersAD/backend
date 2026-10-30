import logging

from django.apps import AppConfig
logger = logging.getLogger(__name__)


class MlflowIntegrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.mlflow_integration'
    verbose_name = 'MLflow Model Orchestration'
    
    def ready(self):
        """Initialize MLflow tracking on app startup (DISABLED)"""
        # MLflow disabled for Railway deployment to prevent startup hangs
        logger.debug('[MLflow] ⚠️  MLflow is disabled for Railway deployment')
        return
