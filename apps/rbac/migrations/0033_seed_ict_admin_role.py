"""
Seed ICT Administrator Role
SOFT-CODED: Reads from rbac_config.py SYSTEM_ROLES_CONFIG
"""
from django.db import migrations
from apps.rbac.rbac_config import SYSTEM_ROLES_CONFIG, ROLE_MODULE_POLICY


def seed_ict_admin_role(apps, schema_editor):
    """
    Create ICT Administrator role and grant admin section modules
    Idempotent - safe to run multiple times
    """
    Role = apps.get_model('rbac', 'Role')
    Module = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    
    # Find ICT Admin role config from rbac_config.py (soft-coded)
    ict_admin_config = next(
        (r for r in SYSTEM_ROLES_CONFIG if r['code'] == 'ict_admin'),
        None
    )
    
    if not ict_admin_config:
        print("⚠️  ICT Admin role not found in SYSTEM_ROLES_CONFIG")
        return
    
    # Create or update ICT Admin role
    ict_admin_role, created = Role.objects.update_or_create(
        code=ict_admin_config['code'],
        defaults={
            'name': ict_admin_config['name'],
            'level': ict_admin_config['level'],
            'description': ict_admin_config['description'],
            'is_system_role': ict_admin_config.get('is_system_role', True),
            'is_active': True,
        }
    )
    
    if created:
        print(f"✅ Created role: {ict_admin_role.name} (code: {ict_admin_role.code}, level: {ict_admin_role.level})")
    else:
        print(f"✅ Updated role: {ict_admin_role.name}")
    
    # Grant modules from ROLE_MODULE_POLICY (soft-coded)
    module_codes = ROLE_MODULE_POLICY.get('ict_admin', [])
    
    if not module_codes:
        print("⚠️  No modules defined in ROLE_MODULE_POLICY for ict_admin")
        return
    
    print(f"\n🔑 Granting {len(module_codes)} admin modules to ICT Administrator role:")
    
    granted_count = 0
    for module_code in module_codes:
        try:
            module = Module.objects.get(code=module_code, is_active=True)
            role_module, created = RoleModule.objects.get_or_create(
                role=ict_admin_role,
                module=module
            )
            
            if created:
                print(f"  ✅ Granted: {module.code:<25} → ict_admin")
                granted_count += 1
            else:
                print(f"  ✅ Exists:  {module.code:<25} → ict_admin")
        except Module.DoesNotExist:
            print(f"  ⚠️  Module not found: {module_code} (will be created by seed_rbac)")
    
    print(f"\n✅ ICT Admin setup complete: {granted_count} new grants, {len(module_codes)} total modules")


def reverse_ict_admin_role(apps, schema_editor):
    """
    Remove ICT Administrator role (rollback)
    """
    Role = apps.get_model('rbac', 'Role')
    
    try:
        ict_admin_role = Role.objects.get(code='ict_admin')
        ict_admin_role.delete()
        print("✅ Removed ICT Administrator role")
    except Role.DoesNotExist:
        print("⚠️  ICT Administrator role not found (already removed)")


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0032_seed_admin_section_modules'),  # Latest migration
    ]

    operations = [
        migrations.RunPython(seed_ict_admin_role, reverse_ict_admin_role),
    ]
