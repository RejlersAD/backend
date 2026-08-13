"""
Migration 0044 — Cross-environment custom role parity.

Local dev was found to be running against a restored snapshot of the preprod
database (see AIFLOW_ENVIRONMENT / DATABASE_URL diagnostics), which is why
custom roles created there (e.g. 'rad102' / Onboarding, used by
lira.viaga@rejlers.ae to access "4.3 Onboarding | Offboarding") were not
present in the live production database — each environment's database is
independent and these roles were never created there.

This migration idempotently re-creates every custom Role (and its granted
Module list) captured from that snapshot into whichever environment runs it.
It is purely additive:
  - Only creates a Role if one with that exact `code` doesn't already exist.
  - Only adds RoleModule grants that are missing; never removes existing ones.
  - Never touches UserRole (role-to-user) assignments — assigning these roles
    to the correct employees in each environment must still be done manually
    via Role & Access Management, since user accounts are not guaranteed to
    match 1:1 across separate databases.
"""
import json
from pathlib import Path
from django.db import migrations

SEED_FILE = Path(__file__).resolve().parent / '_custom_roles_seed_data.json'


def seed_custom_roles(apps, schema_editor):
    Role = apps.get_model('rbac', 'Role')
    Module = apps.get_model('rbac', 'RoleModule').module.field.related_model
    RoleModule = apps.get_model('rbac', 'RoleModule')

    if not SEED_FILE.exists():
        return

    with open(SEED_FILE, 'r') as f:
        roles_data = json.load(f)

    for entry in roles_data:
        role, _ = Role.objects.get_or_create(
            code=entry['code'],
            defaults={
                'name': entry['name'],
                'level': entry['level'],
                'is_system_role': False,
                'is_active': True,
                'auto_sync_enabled': True,
            },
        )

        existing_module_codes = set(
            RoleModule.objects.filter(role=role).values_list('module__code', flat=True)
        )
        missing_codes = set(entry['modules']) - existing_module_codes

        for code in missing_codes:
            module = Module.objects.filter(code=code, is_active=True).first()
            if not module:
                continue
            RoleModule.objects.get_or_create(role=role, module=module)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0043_reseed_modules_and_retry_super_admin'),
    ]

    operations = [
        migrations.RunPython(seed_custom_roles, reverse_code=reverse_noop),
    ]
