"""
Seed script to create modules and organizations
"""
from apps.rbac.models import Module, Organization
import uuid

# Create/Update Modules
modules_data = [
    {
        'code': 'PID',
        'name': '1. Process Engineering - P&ID',
        'description': 'P&ID Design Verification & Analysis',
        'order': 1,
        'icon': 'BeakerIcon'
    },
    {
        'code': 'PFD',
        'name': '1. Process Engineering - PFD',
        'description': 'PFD to P&ID Converter',
        'order': 2,
        'icon': 'DocumentTextIcon'
    },
    {
        'code': 'CRS',
        'name': '2. CRS - Comment Resolution Sheet',
        'description': 'CRS Document Management System',
        'order': 3,
        'icon': 'ChartBarIcon'
    },
    {
        'code': 'PROJECT_CONTROL',
        'name': '3. Project Control',
        'description': 'Project Management & Tracking',
        'order': 4,
        'icon': 'BriefcaseIcon'
    },
]

print('Creating/Updating Modules...')
for mod_data in modules_data:
    module, created = Module.objects.update_or_create(
        code=mod_data['code'],
        defaults={
            'name': mod_data['name'],
            'description': mod_data['description'],
            'order': mod_data['order'],
            'icon': mod_data['icon'],
            'is_active': True
        }
    )
    action = "Created" if created else "Updated"
    print(f'{action}: {module.code} - {module.name}')

# Check organization
org = Organization.objects.first()
if not org:
    org = Organization.objects.create(
        name='Rejlers AB',
        code='REJLERS',
        description='Rejlers Abu Dhabi',
        is_active=True
    )
    print(f'Created Organization: {org.name}')
else:
    print(f'Organization exists: {org.name}')

print('\n=== FINAL MODULE LIST ===')
for m in Module.objects.all():
    print(f'{m.id} | {m.code} | {m.name} | Active: {m.is_active}')

print('\n=== ORGANIZATIONS ===')
for org in Organization.objects.all():
    print(f'{org.id} | {org.code} | {org.name} | Active: {org.is_active}')

print('\nDone!')
