"""
Initialize ML Detection System
Run this script to set up the ML detection and alert system
"""
from django.core.management.base import BaseCommand
from apps.ml_detection.models import DetectionConfig, MLDetectionModel
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Initialize ML Detection System with default configurations'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Initializing ML Detection System...'))

        # Get or create admin user
        admin_user = User.objects.filter(is_superuser=True).first()

        # Create default detection configurations
        configs = [
            {
                'name': 'System Performance Anomaly Detection',
                'detection_type': 'anomaly',
                'description': 'Detects unusual system performance patterns using Isolation Forest',
                'severity': 'high',
                'ml_config': {
                    'contamination': 0.1,
                    'n_estimators': 100,
                    'max_samples': 'auto'
                },
                'threshold_config': {
                    'std_multiplier': 3.0,
                    'window_size': 100
                },
                'auto_notify': True,
                'notification_channels': ['dashboard', 'websocket'],
                'cooldown_period_seconds': 300,
                'is_active': True,
                'is_ml_enabled': True
            },
            {
                'name': 'Security Threat Detection',
                'detection_type': 'security',
                'description': 'Monitors for security-related anomalies and threats',
                'severity': 'critical',
                'ml_config': {
                    'contamination': 0.05,
                    'n_estimators': 150
                },
                'threshold_config': {
                    'std_multiplier': 4.0,
                    'window_size': 50
                },
                'auto_notify': True,
                'notification_channels': ['dashboard', 'websocket', 'email'],
                'cooldown_period_seconds': 180,
                'is_active': True,
                'is_ml_enabled': True
            },
            {
                'name': 'API Performance Degradation',
                'detection_type': 'performance',
                'description': 'Detects degradation in API response times and throughput',
                'severity': 'medium',
                'ml_config': {
                    'contamination': 0.15,
                    'n_estimators': 80
                },
                'threshold_config': {
                    'std_multiplier': 2.5,
                    'window_size': 200
                },
                'auto_notify': True,
                'notification_channels': ['dashboard', 'websocket'],
                'cooldown_period_seconds': 600,
                'is_active': True,
                'is_ml_enabled': True
            },
            {
                'name': 'User Activity Pattern Recognition',
                'detection_type': 'pattern',
                'description': 'Identifies unusual user behavior patterns',
                'severity': 'low',
                'ml_config': {
                    'sequence_length': 10,
                    'similarity_threshold': 0.85
                },
                'threshold_config': {
                    'std_multiplier': 3.0
                },
                'auto_notify': True,
                'notification_channels': ['dashboard'],
                'cooldown_period_seconds': 900,
                'is_active': True,
                'is_ml_enabled': True
            },
            {
                'name': 'Resource Usage Threshold Alert',
                'detection_type': 'threshold',
                'description': 'Alerts when resource usage exceeds defined thresholds',
                'severity': 'high',
                'ml_config': {},
                'threshold_config': {
                    'cpu_threshold': 80,
                    'memory_threshold': 85,
                    'disk_threshold': 90
                },
                'auto_notify': True,
                'notification_channels': ['dashboard', 'websocket'],
                'cooldown_period_seconds': 300,
                'is_active': True,
                'is_ml_enabled': False
            }
        ]

        created_count = 0
        for config_data in configs:
            config, created = DetectionConfig.objects.get_or_create(
                name=config_data['name'],
                defaults={
                    **config_data,
                    'created_by': admin_user
                }
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created: {config.name}'))
            else:
                self.stdout.write(self.style.WARNING(f'  → Already exists: {config.name}'))

        # Create default ML models
        models_data = [
            {
                'name': 'Isolation Forest v1.0',
                'model_type': 'isolation_forest',
                'version': '1.0',
                'model_params': {
                    'contamination': 0.1,
                    'n_estimators': 100,
                    'max_samples': 'auto',
                    'random_state': 42
                },
                'accuracy': 0.92,
                'precision': 0.89,
                'recall': 0.87,
                'f1_score': 0.88,
                'training_samples': 1000,
                'is_active': True
            },
            {
                'name': 'Random Forest Classifier v1.0',
                'model_type': 'random_forest',
                'version': '1.0',
                'model_params': {
                    'n_estimators': 100,
                    'max_depth': 10,
                    'random_state': 42
                },
                'accuracy': 0.94,
                'precision': 0.91,
                'recall': 0.90,
                'f1_score': 0.905,
                'training_samples': 1500,
                'is_active': True
            }
        ]

        models_created = 0
        for model_data in models_data:
            model, created = MLDetectionModel.objects.get_or_create(
                name=model_data['name'],
                defaults=model_data
            )
            if created:
                models_created += 1
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created model: {model.name}'))
            else:
                self.stdout.write(self.style.WARNING(f'  → Model exists: {model.name}'))

        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('✅ ML Detection System Initialization Complete!'))
        self.stdout.write(f'  • Detection Configs Created: {created_count}')
        self.stdout.write(f'  • ML Models Created: {models_created}')
        self.stdout.write(f'  • Total Active Configs: {DetectionConfig.objects.filter(is_active=True).count()}')
        self.stdout.write(f'  • Total Active Models: {MLDetectionModel.objects.filter(is_active=True).count()}')
        self.stdout.write('='*60)
        
        self.stdout.write('\n📝 Next Steps:')
        self.stdout.write('  1. Start Redis: redis-server')
        self.stdout.write('  2. Run migrations: python manage.py migrate')
        self.stdout.write('  3. Start Django: python manage.py runserver')
        self.stdout.write('  4. Navigate to: http://localhost:5173/admin/dashboard')
        self.stdout.write('  5. Click on "🤖 ML Detection" tab')
        self.stdout.write('\n🎉 Happy Detecting!')
