from django.apps import AppConfig


class MlflowIntegrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.mlflow_integration'
    verbose_name = 'MLflow Model Orchestration'
    
    def ready(self):
        """Initialize MLflow tracking on app startup"""
        import apps.mlflow_integration.signals  # noqa
        from apps.mlflow_integration.tracker import MLflowTracker
        
        # Initialize MLflow with configuration
        tracker = MLflowTracker()
        tracker.initialize()
        
        print('[MLflow] ✅ Model orchestration initialized')
