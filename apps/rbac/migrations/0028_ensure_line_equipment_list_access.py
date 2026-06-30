"""
Migration 0028 — Ensure Line List & Equipment List Access for viewer / default roles
=====================================================================================
Root cause addressed:
  In some production deployments, migration 0024 (which seeds pid_line_list and
  pid_equipment_list) may have run before the 'viewer' role existed (or vice-versa),
  leaving RoleModule rows missing for those two modules on the viewer/default roles.

This migration idempotently ensures:
  1. pid_line_list and pid_equipment_list Module objects exist and are active.
  2. Both the 'viewer' and 'default' roles have RoleModule rows for these two modules.
  3. Redis module-cache keys for all affected users are invalidated so the fix
     takes effect immediately without requiring a cache TTL expiry (60 s).

All operations are safe to re-run (get_or_create throughout).
"""
import uuid
from django.db import migrations

# Soft-coded: the two module codes that must be accessible to every regular user
REQUIRED_MODULE_CODES = ['pid_line_list', 'pid_equipment_list']

# Soft-coded: role codes that represent the "general access" tier
TARGET_ROLE_CODES = ['viewer', 'default']


def ensure_line_equipment_list_access(apps, schema_editor):
    Module      = apps.get_model('rbac', 'Module')
    Role        = apps.get_model('rbac', 'Role')
    RoleModule  = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole    = apps.get_model('rbac', 'UserRole')
    db_alias    = schema_editor.connection.alias

    # ------------------------------------------------------------------
    # 1. Ensure the Module objects exist and are active
    # ------------------------------------------------------------------
    MODULE_DEFAULTS = {
        'pid_line_list': {
            'name':        'Line List',
            'description': 'Extract 8 base columns from P&ID (P&ID-only, no enrichment)',
            'icon':        'TableCells',
            'order':       22,
        },
        'pid_equipment_list': {
            'name':        'Equipment List',
            'description': 'Extract equipment tags and type classification from P&ID',
            'icon':        'TableCells',
            'order':       23,
        },
    }

    module_objs = {}
    for code in REQUIRED_MODULE_CODES:
        obj, created = Module.objects.using(db_alias).get_or_create(
            code=code,
            defaults={**MODULE_DEFAULTS[code], 'is_active': True},
        )
        # Ensure existing module is marked active
        if not obj.is_active:
            obj.is_active = True
            obj.save(using=db_alias, update_fields=['is_active'])
        module_objs[code] = obj
        print(f'  [0028] ✓ Module {"created" if created else "verified"}: {code}')

    # ------------------------------------------------------------------
    # 2. Ensure each target role has RoleModule rows for both modules
    # ------------------------------------------------------------------
    for role_code in TARGET_ROLE_CODES:
        try:
            role = Role.objects.using(db_alias).get(code=role_code, is_active=True)
        except Role.DoesNotExist:
            print(f'  [0028] ⚠ Role "{role_code}" not found or inactive — skipping')
            continue

        for code, mod_obj in module_objs.items():
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role,
                module=mod_obj,
                defaults={'id': uuid.uuid4(), 'granted_by': None},
            )
            status = 'granted' if created else 'already had'
            print(f'  [0028] ✓ {role_code} {status} {code}')

    # ------------------------------------------------------------------
    # 3. Flush Redis module-cache keys for every user on a target role
    #    so the fix takes effect immediately (non-fatal if Redis absent)
    # ------------------------------------------------------------------
    try:
        import redis as redis_lib
        import os

        # Discover all UserProfile IDs whose primary role is one of the targets
        profile_ids = list(
            UserRole.objects.using(db_alias)
            .filter(role__code__in=TARGET_ROLE_CODES, is_primary=True)
            .values_list('user_profile_id', flat=True)
        )

        # Also flush every profile that has any UserRole on a target role
        all_profile_ids = list(
            UserRole.objects.using(db_alias)
            .filter(role__code__in=TARGET_ROLE_CODES)
            .values_list('user_profile_id', flat=True)
        )

        all_ids = list(set(profile_ids) | set(all_profile_ids))

        if all_ids:
            redis_host     = os.environ.get('REDIS_HOST', 'redis')
            redis_port     = int(os.environ.get('REDIS_PORT', 6379))
            redis_password = os.environ.get('REDIS_PASSWORD', None)
            r = redis_lib.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                socket_connect_timeout=3,
                decode_responses=True,
            )
            flushed = 0
            for pid in all_ids:
                key = f'user_modules_{pid}'
                if r.delete(key):
                    flushed += 1
            print(f'  [0028] ✓ Flushed {flushed} Redis module-cache key(s) for {len(all_ids)} profile(s)')
    except Exception as exc:
        # Non-fatal: cache will expire on its own within 60 s
        print(f'  [0028] ⚠ Redis flush skipped (non-fatal): {exc}')

    print('\n[0028] Done — Line List & Equipment List access ensured for viewer + default roles.')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0027_seed_default_role'),
    ]

    operations = [
        migrations.RunPython(ensure_line_equipment_list_access, noop),
    ]
