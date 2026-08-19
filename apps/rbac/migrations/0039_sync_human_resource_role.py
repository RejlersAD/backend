# Generated migration to sync human_resource role modules

from django.db import migrations


def sync_human_resource_role(apps, schema_editor):
    """
    Fix human_resource role by removing extra modules that don't belong.
    
    Background: The human_resource role had 4 extra modules assigned that aren't in 
    ROLE_MODULE_POLICY. This migration syncs the database to match the policy.
    """
    Role = apps.get_model('rbac', 'Role')
    Module = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    
    try:
        role = Role.objects.get(code='human_resource')
    except Role.DoesNotExist:
        print("  [SKIP] human_resource role not found")
        return
    
    # Expected HR modules according to ROLE_MODULE_POLICY
    expected_modules = [
        'hr_management',
        'payroll',
        'timesheet',
        'hr_self_service',
        'hr_onboarding',
    ]
    
    # Modules to remove (extras that don't belong)
    modules_to_remove = [
        'crs_documents',
        'pfd_to_pid',
        'designiq',
        'data_mining',
    ]
    
    # Remove extra modules
    removed_count = 0
    for module_code in modules_to_remove:
        try:
            module = Module.objects.get(code=module_code)
            deleted = RoleModule.objects.filter(role=role, module=module).delete()
            if deleted[0] > 0:
                removed_count += 1
                print(f"  - Removed module: {module_code}")
        except Module.DoesNotExist:
            pass
    
    # Verify expected modules are present
    missing_count = 0
    for module_code in expected_modules:
        try:
            module = Module.objects.get(code=module_code)
            rm, created = RoleModule.objects.get_or_create(role=role, module=module)
            if created:
                missing_count += 1
                print(f"  + Added module: {module_code}")
        except Module.DoesNotExist:
            print(f"  ! Module not found: {module_code}")
    
    # Summary
    current_count = RoleModule.objects.filter(role=role).count()
    print(f"  ✓ Sync complete: removed {removed_count}, added {missing_count}")
    print(f"  ✓ Final module count: {current_count} (expected: {len(expected_modules)})")


def reverse_sync(apps, schema_editor):
    """
    Reverse migration - no action needed as this is a data fix.
    """
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0038_rename_activity_user_ts_idx_activity_ev_user_id_f3fd17_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(sync_human_resource_role, reverse_sync),
    ]
