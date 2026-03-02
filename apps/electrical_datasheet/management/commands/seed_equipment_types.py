"""
Management command to seed electrical equipment types from configuration
Usage: python manage.py seed_equipment_types
"""

from django.core.management.base import BaseCommand
from apps.electrical_datasheet.models import ElectricalEquipmentType
from apps.electrical_datasheet.equipment_types_config import EQUIPMENT_TYPES_CONFIG


class Command(BaseCommand):
    help = 'Seed electrical equipment types from soft-coded configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing equipment types',
        )
        parser.add_argument(
            '--delete-orphans',
            action='store_true',
            help='Delete equipment types not in configuration',
        )

    def handle(self, *args, **options):
        update_existing = options['update']
        delete_orphans = options['delete_orphans']
        
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write(self.style.HTTP_INFO('  Seeding Electrical Equipment Types'))
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write('')
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        # Process each equipment type from configuration
        for config in EQUIPMENT_TYPES_CONFIG:
            equipment_id = config['id']
            
            try:
                # Try to get existing equipment type
                equipment_type = ElectricalEquipmentType.objects.filter(id=equipment_id).first()
                
                if equipment_type:
                    if update_existing:
                        # Update existing
                        equipment_type.name = config['name']
                        equipment_type.code = config['code']
                        equipment_type.description = config['description']
                        equipment_type.icon = config['icon']
                        equipment_type.category = config['category']
                        equipment_type.standards = config['standards']
                        equipment_type.sections = config['sections']
                        equipment_type.is_active = True
                        equipment_type.save()
                        
                        self.stdout.write(
                            self.style.WARNING(f'  ✓ Updated: {equipment_type.name} ({equipment_type.code})')
                        )
                        updated_count += 1
                    else:
                        self.stdout.write(
                            self.style.HTTP_INFO(f'  ○ Skipped (exists): {config["name"]} ({config["code"]})')
                        )
                        skipped_count += 1
                else:
                    # Create new equipment type
                    equipment_type = ElectricalEquipmentType.objects.create(
                        id=config['id'],
                        name=config['name'],
                        code=config['code'],
                        description=config['description'],
                        icon=config['icon'],
                        category=config['category'],
                        standards=config['standards'],
                        sections=config['sections'],
                        is_active=True
                    )
                    
                    self.stdout.write(
                        self.style.SUCCESS(f'  ✓ Created: {equipment_type.name} ({equipment_type.code})')
                    )
                    created_count += 1
                    
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Error processing {config["name"]}: {str(e)}')
                )
        
        # Delete orphaned equipment types if requested
        deleted_count = 0
        if delete_orphans:
            config_ids = [config['id'] for config in EQUIPMENT_TYPES_CONFIG]
            orphaned = ElectricalEquipmentType.objects.exclude(id__in=config_ids)
            
            for orphan in orphaned:
                self.stdout.write(
                    self.style.WARNING(f'  ✗ Deleting orphaned: {orphan.name} ({orphan.code})')
                )
                orphan.delete()
                deleted_count += 1
        
        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write(self.style.HTTP_INFO('  Summary'))
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write(self.style.SUCCESS(f'  Created: {created_count}'))
        
        if update_existing:
            self.stdout.write(self.style.WARNING(f'  Updated: {updated_count}'))
        else:
            self.stdout.write(self.style.HTTP_INFO(f'  Skipped (existing): {skipped_count}'))
        
        if delete_orphans:
            self.stdout.write(self.style.ERROR(f'  Deleted: {deleted_count}'))
        
        total = ElectricalEquipmentType.objects.filter(is_active=True).count()
        self.stdout.write(self.style.SUCCESS(f'  Total active types: {total}'))
        self.stdout.write('')
        
        # List all equipment types by category
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write(self.style.HTTP_INFO('  Equipment Types by Category'))
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        
        categories = {}
        for equipment_type in ElectricalEquipmentType.objects.filter(is_active=True).order_by('category', 'name'):
            if equipment_type.category not in categories:
                categories[equipment_type.category] = []
            categories[equipment_type.category].append(equipment_type)
        
        for category, types in categories.items():
            self.stdout.write('')
            self.stdout.write(self.style.HTTP_INFO(f'  {category}:'))
            for eq_type in types:
                self.stdout.write(f'    • {eq_type.icon} {eq_type.name} ({eq_type.code})')
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('✓ Equipment types seeding completed successfully!'))
        self.stdout.write('')
