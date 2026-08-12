# Migration to clean up ALL unwanted roles regardless of is_system_role flag
# Targets BOTH system and non-system variants of the unwanted roles

from django.db import migrations


def cleanup_all_unwanted_roles(apps, schema_editor):
    Role = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')

    roles_to_delete = [
        'civil_engineer',
        'mechanical_engineer',
        'process_engineer',
        'electrical_engineer',
        'instrument_engineer',
        'piping_engineer',
        'design_engineer',
        'human_resource',
        'engineer',
        'onboarding',
        'ict_admin',
        'admin_it',   # non-system ICT Admin variant
    ]

    deleted = 0
    for code in roles_to_delete:
        # Use filter (not get) to handle duplicates; skip any role that has users
        for role in Role.objects.filter(code=code, is_active=True):
            # Check for assigned users via reverse relation or RoleModule
            has_users = role.user_profiles.exists() if hasattr(role, 'user_profiles') else False
            if not has_users:
                # Try the UserProfile FK as well
                try:
                    from django.contrib.contenttypes.models import ContentType
                    has_users = role.user_set.exists() if hasattr(role, 'user_set') else False
                except Exception:
                    pass

            if has_users:
                print(f"  ⚠ Skipping {role.name} ({code}) — has assigned users")
                continue

            RoleModule.objects.filter(role=role).delete()
            role.delete()
            deleted += 1
            print(f"  ✓ Deleted: {role.name} ({code})")

    print(f"\n  Cleanup complete: {deleted} roles deleted")

    # Print final state
    remaining = Role.objects.filter(is_active=True).exclude(code__startswith='custom_').order_by('name')
    print(f"  Remaining active roles ({remaining.count()}):")
    for r in remaining:
        print(f"    - {r.name} ({r.code})")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0040_delete_unused_engineering_roles'),
    ]

    operations = [
        migrations.RunPython(cleanup_all_unwanted_roles, noop_reverse),
    ]
