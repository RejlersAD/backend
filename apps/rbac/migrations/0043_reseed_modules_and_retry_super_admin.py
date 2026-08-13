"""
Migration 0043 — Preprod data-repair.

Two idempotent, additive-only fixes for environments (like preprod) whose
database was never fully seeded:

1. Seed any ALL_MODULES_CATALOGUE entry missing from the Module table. The
   catalogue is normally lazy-synced only when /rbac/modules/ (Role
   Management admin page) is hit — if nobody ever opened that page in this
   environment, Module stayed empty and even the super_admin bypass had
   nothing to return.
2. Retry "ensure tanzeem.agra@rejlers.ae is super_admin" (originally
   migration 0017). That migration is a no-op if the target user doesn't
   exist yet at the time it runs; if the account was created afterwards,
   it was silently skipped forever. Re-running the same idempotent logic
   here catches that case.
"""
from django.db import migrations

TARGET_EMAIL = 'tanzeem.agra@rejlers.ae'
SUPER_ADMIN_ROLE_CODE = 'super_admin'


def reseed_modules(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE

    existing_codes = set(Module.objects.values_list('code', flat=True))
    missing = [m for m in ALL_MODULES_CATALOGUE if m['code'] not in existing_codes]
    if not missing:
        return
    Module.objects.bulk_create(
        [
            Module(
                code=m['code'],
                name=m['name'],
                description=m.get('description', ''),
                icon=m.get('icon', ''),
                order=m.get('order', 0),
                is_active=True,
            )
            for m in missing
        ],
        ignore_conflicts=True,
    )


def retry_super_admin(apps, schema_editor):
    User = apps.get_model('users', 'User')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole = apps.get_model('rbac', 'UserRole')
    Role = apps.get_model('rbac', 'Role')

    try:
        user = User.objects.get(email=TARGET_EMAIL)
    except User.DoesNotExist:
        return

    changed = False
    if not user.is_superuser:
        user.is_superuser = True
        changed = True
    if not user.is_staff:
        user.is_staff = True
        changed = True
    if changed:
        user.save()

    try:
        role = Role.objects.get(code=SUPER_ADMIN_ROLE_CODE)
    except Role.DoesNotExist:
        return

    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        return

    UserRole.objects.get_or_create(
        user_profile=profile,
        role=role,
        defaults={'is_primary': True},
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0042_role_auto_sync_enabled'),
    ]

    operations = [
        migrations.RunPython(reseed_modules, reverse_code=reverse_noop),
        migrations.RunPython(retry_super_admin, reverse_code=reverse_noop),
    ]
