# Generated migration to delete unused engineering and HR roles

from django.db import migrations


def delete_unused_roles(apps, schema_editor):
    """
    Delete unused engineering discipline roles and HR roles from the database.
    
    These roles were removed from SYSTEM_ROLES_CONFIG and ROLE_MODULE_POLICY:
    - Engineering discipline roles (process, electrical, instrument, mechanical, civil, piping, design)
    - Human Resource role (replaced by hr_admin)
    - Onboarding role (specialized HR role no longer needed)
    - Engineer custom role (generic role no longer needed)
    """
    Role = apps.get_model('rbac', 'Role')
    UserRole = apps.get_model('rbac', 'UserRole')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    
    # Roles to delete
    roles_to_delete = [
        'civil_engineer',
        'mechanical_engineer', 
        'process_engineer',
        'human_resource',
        'piping_engineer',
        'instrument_engineer',
        'electrical_engineer',
        'design_engineer',
        'engineer',  # Custom role
        'onboarding',  # Specialized HR role
        'ict_admin',  # ICT Administrator role
    ]
    
    deleted_count = 0
    for role_code in roles_to_delete:
        try:
            role = Role.objects.get(code=role_code)
            
            # Check if any users are assigned this role
            user_count = UserRole.objects.filter(role=role).count()
            if user_count > 0:
                print(f"  ⚠️ Skipping {role.name} ({role_code}) - {user_count} users assigned")
                continue
            
            # Delete role-module assignments
            module_count = RoleModule.objects.filter(role=role).count()
            RoleModule.objects.filter(role=role).delete()
            
            # Delete the role
            role.delete()
            deleted_count += 1
            print(f"  ✓ Deleted role: {role.name} ({role_code}) - {module_count} modules removed")
            
        except Role.DoesNotExist:
            print(f"  • Role not found: {role_code} (skipping)")
    
    print(f"\n  ✓ Cleanup complete: {deleted_count} roles deleted")


def reverse_delete(apps, schema_editor):
    """
    Reverse migration - cannot restore deleted roles.
    """
    print("  ⚠️ Reverse migration not supported - roles cannot be automatically restored")


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0039_sync_human_resource_role'),
    ]

    operations = [
        migrations.RunPython(delete_unused_roles, reverse_delete),
    ]
