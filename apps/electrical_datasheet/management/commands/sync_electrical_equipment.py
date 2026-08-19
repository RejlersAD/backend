from django.core.management.base import BaseCommand
from apps.electrical_datasheet.models import ElectricalEquipmentType
import json
import os


class Command(BaseCommand):
    help = 'Sync electrical equipment types from configuration file to database'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting equipment type synchronization...'))
        
        # Load configuration file
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            '..',
            'process_datasheet',
            'electrical_datasheet_config.json'
        )
        
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f'Configuration file not found: {config_path}'))
            return
        except json.JSONDecodeError:
            self.stdout.write(self.style.ERROR('Invalid JSON in configuration file'))
            return
        
        equipment_types = config_data.get('equipment_types', {})
        
        if not equipment_types:
            self.stdout.write(self.style.WARNING('No equipment types found in configuration'))
            return
        
        synced_count = 0
        created_count = 0
        updated_count = 0
        
        for eq_id, eq_data in equipment_types.items():
            equipment_type, created = ElectricalEquipmentType.objects.update_or_create(
                id=eq_id,
                defaults={
                    'name': eq_data.get('name'),
                    'code': eq_data.get('code'),
                    'description': eq_data.get('description', ''),
                    'icon': eq_data.get('icon', ''),
                    'category': eq_data.get('category', ''),
                    'standards': eq_data.get('standards', []),
                    'sections': eq_data.get('sections', []),
                    'is_active': True
                }
            )
            
            synced_count += 1
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ Created: {equipment_type.name} ({equipment_type.code})')
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  ↻ Updated: {equipment_type.name} ({equipment_type.code})')
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSynchronization complete!\n'
                f'  Total synced: {synced_count}\n'
                f'  Created: {created_count}\n'
                f'  Updated: {updated_count}'
            )
        )
