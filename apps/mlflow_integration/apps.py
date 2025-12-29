from django.apps import AppConfig


class MlflowIntegrationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.mlflow_integration'
    verbose_name = 'MLflow Model Orchestration'
    
    def ready(self):
        """Initialize MLflow tracking on app startup"""
        import os
        from django.conf import settings
        
        # Skip MLflow initialization in development if MLflow service is not available
        if settings.DEBUG and not os.environ.get('MLFLOW_TRACKING_URI'):
            print('[MLflow] ⚠️  Skipped initialization (development mode without MLflow service)')
            return
            
        try:
            import apps.mlflow_integration.signals  # noqa
            from apps.mlflow_integration.tracker import MLflowTracker
            
            # Initialize MLflow with configuration
            tracker = MLflowTracker()
            tracker.initialize()
            
            print('[MLflow] ✅ Model orchestration initialized')
        except Exception as e:
            print(f'[MLflow] ⚠️  Initialization failed: {e}')
            print('[MLflow] Continuing without MLflow tracking...')
