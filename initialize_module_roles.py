"""
Soft-Coded Module Role Initialization Script
Ensures every module has a corresponding role for proper RBAC functionality
"""
from apps.rbac.models import Module, Role

# Soft-coded module-to-role template configuration
MODULE_ROLE_TEMPLATES = {
    'procurement': {
        'name': 'Procurement Manager',
        'code': 'procurement_manager',
        'description': 'Full access to procurement management features including vendors, requisitions, purchase orders, and goods receipt',
        'level': 30
    },
    'qhse': {
        'name': 'QHSE Manager',
        'code': 'qhse_manager',
        'description': 'Full access to QHSE (Quality, Health, Safety, Environment) management features',
        'level': 30
    },
    'finance': {
        'name': 'Finance Manager',
        'code': 'finance_manager',
        'description': 'Full access to finance and invoice management features',
        'level': 30
    },
    'project_control': {
        'name': 'Project Controller',
        'code': 'project_controller',
        'description': 'Full access to project control and tracking features',
        'level': 30
    },
    'crs_documents': {
        'name': 'CRS Document Manager',
        'code': 'crs_manager',
        'description': 'Full access to CRS document management features',
        'level': 30
    },
    'pid_analysis': {
        'name': 'PID Analyst',
        'code': 'pid_analyst',
        'description': 'Access to P&ID analysis and verification features',
        'level': 30
    },
    'pfd_to_pid': {
        'name': 'PFD Engineer',
        'code': 'pfd_engineer',
        'description': 'Access to PFD to P&ID conversion features',
        'level': 30
    },
    'designiq': {
        'name': 'Design Engineer',
        'code': 'design_engineer',
        'description': 'Access to DesignIQ AI design optimization features',
        'level': 30
    }
}

print('\n' + '='*60)
print('MODULE ROLE INITIALIZATION SCRIPT')
print('='*60)

# Process each module
for module_code, role_template in MODULE_ROLE_TEMPLATES.items():
    print(f'\n📦 Processing module: {module_code}')
    
    # Check if module exists
    module = Module.objects.filter(code=module_code).first()
    if not module:
        print(f'   ⚠️  Module "{module_code}" not found in database, skipping...')
        continue
    
    print(f'   ✅ Found module: {module.name}')
    
    # Check if role already exists
    existing_role = Role.objects.filter(code=role_template['code']).first()
    if existing_role:
        print(f'   ℹ️  Role "{existing_role.name}" already exists')
        # Ensure module is assigned to role
        if module not in existing_role.modules.all():
            existing_role.modules.add(module)
            print(f'   ✅ Assigned module to existing role')
        else:
            print(f'   ✅ Module already assigned to role')
        continue
    
    # Check if module has ANY roles
    existing_roles_count = module.roles.count()
    if existing_roles_count > 0:
        print(f'   ✅ Module already has {existing_roles_count} role(s) assigned')
        continue
    
    # Create new role
    print(f'   📝 Creating new role: {role_template["name"]}')
    new_role = Role.objects.create(
        name=role_template['name'],
        code=role_template['code'],
        description=role_template['description'],
        level=role_template['level'],
        is_active=True
    )
    
    # Assign module to role
    new_role.modules.add(module)
    print(f'   ✅ Created role and assigned module')

print('\n' + '='*60)
print('SUMMARY')
print('='*60)

# Show final status
all_modules = Module.objects.filter(is_active=True)
print(f'\nTotal active modules: {all_modules.count()}')
print('\nModule → Role Status:')
for module in all_modules:
    role_count = module.roles.count()
    status = '✅' if role_count > 0 else '⚠️'
    print(f'{status} {module.code.ljust(20)} → {role_count} role(s)')

print('\n✅ MODULE ROLE INITIALIZATION COMPLETE\n')
