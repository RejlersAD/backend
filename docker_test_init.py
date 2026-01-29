#!/usr/bin/env python
"""
Initialize test environment with superuser and equipment configurations
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.process_datasheet.models import EquipmentType
import json
from pathlib import Path

User = get_user_model()

def create_test_user():
    """Create test superuser if not exists"""
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'admin@test.com', 'admin123')
        print('✅ Test superuser created: admin / admin123')
    else:
        print('ℹ️  Test superuser already exists')

def load_equipment_configs():
    """Load equipment configurations if not exists"""
    config_path = Path('config/equipment_configs/control_valve.json')
    if config_path.exists() and not EquipmentType.objects.filter(equipment_class='control_valve').exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
                EquipmentType.objects.create(**config)
            print('✅ Equipment configurations loaded')
        except Exception as e:
            print(f'⚠️  Failed to load equipment configs: {e}')
    else:
        print('ℹ️  Equipment configurations already exist or file not found')

if __name__ == '__main__':
    print('=== Initializing test environment ===')
    create_test_user()
    load_equipment_configs()
    print('=== Initialization complete ===')
