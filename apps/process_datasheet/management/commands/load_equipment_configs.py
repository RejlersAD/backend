"""
Management command to load equipment type configurations
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.process_datasheet.models import EquipmentType, DatasheetTemplate
from apps.process_datasheet.equipment_configs import CONTROL_VALVE_CONFIG

User = get_user_model()


class Command(BaseCommand):
    help = 'Load equipment type configurations and create default templates'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('\n=== Loading Equipment Type Configurations ===\n'))

        # Get the first admin user for template creation
        system_user = User.objects.filter(is_staff=True, is_active=True).first()
        if not system_user:
            self.stdout.write(self.style.ERROR('No admin user found. Please create an admin user first.'))
            return
        self.stdout.write(self.style.SUCCESS(f'Using user: {system_user.email}'))

        # Load Control Valve Configuration
        self.stdout.write('\n1. Loading Control Valve Configuration...')
        
        control_valve, created = EquipmentType.objects.update_or_create(
            code='CONTROL_VALVE',
            defaults={
                'name': CONTROL_VALVE_CONFIG['name'],
                'description': CONTROL_VALVE_CONFIG['description'],
                'category': CONTROL_VALVE_CONFIG['category'],
                'icon': CONTROL_VALVE_CONFIG.get('icon', '🔧'),
                'version': CONTROL_VALVE_CONFIG.get('version', '1.0'),
                'configuration': CONTROL_VALVE_CONFIG,  # Store entire config
                'status': 'active'
            }
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS(f'   ✓ Created: {control_valve.name}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'   ✓ Updated: {control_valve.name}'))
        
        # Create default template for Control Valve
        self.stdout.write('\n2. Creating Default Templates...')
        
        cv_template, created = DatasheetTemplate.objects.update_or_create(
            equipment_type=control_valve,
            name='ADNOC Control Valve Standard Template',
            defaults={
                'description': 'Standard template for ADNOC control valve datasheets compliant with DEP 31.38.01.32-Gen',
                'template_data': {
                    'operating_conditions': {
                        'normal_pressure_inlet': {'value': '', 'unit': 'bar g'},
                        'normal_pressure_outlet': {'value': '', 'unit': 'bar g'},
                        'design_pressure_inlet': {'value': '', 'unit': 'bar g'},
                        'design_pressure_outlet': {'value': '', 'unit': 'bar g'},
                        'normal_temperature': {'value': '', 'unit': '°C'},
                        'design_temperature': {'value': '', 'unit': '°C'},
                        'fluid_type': '',
                        'fluid_phase': '',
                        'molecular_weight': {'value': '', 'unit': 'kg/kmol'}
                    },
                    'control_parameters': {
                        'normal_flow_rate': {'value': '', 'unit': 'm³/h'},
                        'maximum_flow_rate': {'value': '', 'unit': 'm³/h'},
                        'control_action': 'Fail Close',
                        'rangeability': 50,
                        'cv_required': {'value': '', 'calculated': True}
                    },
                    'valve_body': {
                        'body_material': 'CF8M (SS316)',
                        'trim_material': 'SS316',
                        'seat_material': 'SS316',
                        'body_rating': 'ANSI 300',
                        'end_connection': 'RF',
                        'valve_size': {'value': '', 'unit': 'inch'}
                    },
                    'actuator': {
                        'actuator_type': 'Pneumatic',
                        'actuator_action': 'Spring Return',
                        'supply_pressure': {'value': '6', 'unit': 'bar g'},
                        'positioner_required': True,
                        'solenoid_valve': True
                    },
                    'standards': {
                        'design_standard': 'ASME B16.34',
                        'testing_standard': 'API 598',
                        'face_to_face': 'ASME B16.10'
                    }
                },
                'created_by': system_user
            }
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS(f'   ✓ Created template: {cv_template.name}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'   ✓ Updated template: {cv_template.name}'))

        # Summary
        self.stdout.write(self.style.SUCCESS('\n=== Configuration Loading Complete ==='))
        self.stdout.write(f'Equipment Types: {EquipmentType.objects.count()}')
        self.stdout.write(f'Templates: {DatasheetTemplate.objects.count()}')
        self.stdout.write(self.style.SUCCESS('\n✓ System is ready for datasheet creation!\n'))
