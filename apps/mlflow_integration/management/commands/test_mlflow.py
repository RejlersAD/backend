"""
Django management command to test MLflow integration
"""

from django.core.management.base import BaseCommand
from apps.mlflow_integration.tracker import mlflow_tracker
import mlflow


class Command(BaseCommand):
    help = 'Test MLflow integration and connectivity'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('\n=== MLflow Integration Test ===\n'))
        
        # Check if MLflow is enabled
        if not mlflow_tracker.enabled:
            self.stdout.write(self.style.WARNING('⚠️  MLflow tracking is disabled'))
            self.stdout.write('Set MLFLOW_ENABLED=true to enable tracking\n')
            return
        
        self.stdout.write(self.style.SUCCESS(f'✅ MLflow enabled'))
        self.stdout.write(f'Tracking URI: {mlflow_tracker.tracking_uri}\n')
        
        # Test connection
        try:
            uri = mlflow.get_tracking_uri()
            self.stdout.write(self.style.SUCCESS(f'✅ Connected to tracking server: {uri}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Failed to connect: {e}'))
            return
        
        # List experiments
        try:
            self.stdout.write('\n📊 Available Experiments:')
            for exp_key, exp_name in mlflow_tracker.experiments.items():
                try:
                    exp = mlflow.get_experiment_by_name(exp_name)
                    if exp:
                        self.stdout.write(f'  ✓ {exp_name} (ID: {exp.experiment_id})')
                    else:
                        self.stdout.write(f'  - {exp_name} (not created yet)')
                except Exception:
                    self.stdout.write(f'  - {exp_name} (not created yet)')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Failed to list experiments: {e}'))
        
        # Test logging
        try:
            self.stdout.write('\n🧪 Testing experiment logging...')
            
            mlflow_tracker.log_experiment_result(
                feature='pid_verification',
                operation='test_operation',
                params={'test': 'true', 'command': 'test_mlflow'},
                metrics={'test_metric': 1.0, 'success': 1},
                tags={'source': 'management_command', 'test': 'true'}
            )
            
            self.stdout.write(self.style.SUCCESS('✅ Successfully logged test experiment'))
            self.stdout.write(f'\nView in MLflow UI: {mlflow_tracker.tracking_uri}')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Failed to log experiment: {e}'))
        
        self.stdout.write(self.style.SUCCESS('\n=== Test Complete ===\n'))
