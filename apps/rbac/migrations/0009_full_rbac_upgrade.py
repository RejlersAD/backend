"""
Migration 0009 — Full RBAC Upgrade
===================================
Cross-verified against:
  - user_management/ (standalone RBAC microservice package)
  - data-management/ (document/dataset microservice)

What this migration does (all idempotent / safe to re-run):
  1. Ensure the default organization has a code (`REJ_UAE`)
  2. Create any missing modules from ALL_MODULES_CATALOGUE
     (adds the 6 previously-missing admin modules:
      user_mgmt, org_settings, audit_logs, file_storage, reports, api_access)
  3. Create all system roles from SYSTEM_ROLES_CONFIG
     (super_admin, admin, process_engineer, electrical_engineer,
      instrument_engineer, mechanical_engineer, civil_engineer,
      piping_engineer, qhse_engineer, design_engineer,
      project_manager, viewer)
  4. Mark all seeded roles as is_system_role=True
  5. Assign modules to roles per ROLE_MODULE_POLICY
     (super_admin and admin get ALL modules)
  6. Fix the orphan "Admin" role (empty code) → set code='admin',
     merge its user assignments into the proper admin role, then delete orphan
  7. Fix tanzeem.agra@rejlers.ae → assign super_admin role
"""
from django.db import migrations


# ── Pull config from rbac_config (available at migration time) ────────────────
def _get_config():
    from apps.rbac.rbac_config import (
        ALL_MODULES_CATALOGUE,
        SYSTEM_ROLES_CONFIG,
        ROLE_MODULE_POLICY,
    )
    return ALL_MODULES_CATALOGUE, SYSTEM_ROLES_CONFIG, ROLE_MODULE_POLICY


def upgrade_rbac(apps, schema_editor):
    Organization = apps.get_model('rbac', 'Organization')
    Module = apps.get_model('rbac', 'Module')
    Role = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole = apps.get_model('rbac', 'UserRole')
    User = apps.get_model('users', 'User')

    ALL_MODULES_CATALOGUE, SYSTEM_ROLES_CONFIG, ROLE_MODULE_POLICY = _get_config()

    # ── 1. Fix Organization code ─────────────────────────────────────────────
    for org in Organization.objects.filter(code=''):
        org.code = 'REJ_UAE'
        org.save(update_fields=['code'])
        print(f'  [0009] Fixed org code → REJ_UAE for: {org.name}')

    # Ensure default org exists — look up by code (safe after the fix above)
    default_org = Organization.objects.filter(code='REJ_UAE').first()
    if default_org is None:
        default_org = Organization.objects.create(
            name='Rejlers Abu Dhabi',
            code='REJ_UAE',
            description='Default organization for Rejlers Oil & Gas AI Platform',
            s3_bucket_name='user-management-rejlers',
            s3_region='us-east-1',
            is_active=True,
            primary_contact_name='Admin',
            primary_contact_email='admin@rejlers.ae',
            city='Abu Dhabi',
            country='United Arab Emirates',
        )
        print(f'  [0009] Created default org: {default_org.name}')

    # ── 2. Create missing modules ─────────────────────────────────────────────
    modules_map = {}
    for entry in ALL_MODULES_CATALOGUE:
        mod, created = Module.objects.get_or_create(
            code=entry['code'],
            defaults={
                'name': entry['name'],
                'description': entry.get('description', ''),
                'icon': entry.get('icon', ''),
                'order': entry.get('order', 0),
                'is_active': True,
            }
        )
        modules_map[mod.code] = mod
        if created:
            print(f'  [0009] ✓ Created module: {mod.name} ({mod.code})')

    # Also pick up any modules already in DB that aren't in the catalogue
    for mod in Module.objects.all():
        modules_map[mod.code] = mod

    # ── 3 & 4. Create / update system roles ──────────────────────────────────
    roles_map = {}
    for role_cfg in SYSTEM_ROLES_CONFIG:
        role, created = Role.objects.get_or_create(
            code=role_cfg['code'],
            defaults={
                'name': role_cfg['name'],
                'level': role_cfg['level'],
                'description': role_cfg['description'],
                'is_system_role': True,
                'is_active': True,
            }
        )
        if created:
            print(f'  [0009] ✓ Created role: {role.name} ({role.code})')
        else:
            # Ensure system flag + level are correct for existing roles
            changed = False
            if not role.is_system_role:
                role.is_system_role = True
                changed = True
            if role.level != role_cfg['level']:
                role.level = role_cfg['level']
                changed = True
            if changed:
                role.save(update_fields=['is_system_role', 'level'])
        roles_map[role.code] = role

    # ── 5. Assign modules to roles per policy ────────────────────────────────
    all_module_codes = list(modules_map.keys())

    for role_code, role in roles_map.items():
        if role_code in ('super_admin', 'admin'):
            module_codes = all_module_codes
        else:
            module_codes = ROLE_MODULE_POLICY.get(role_code, [])

        new_count = 0
        for module_code in module_codes:
            if module_code in modules_map:
                _, created = RoleModule.objects.get_or_create(
                    role=role,
                    module=modules_map[module_code]
                )
                if created:
                    new_count += 1
        if new_count:
            print(f'  [0009]   → Assigned {new_count} new modules to {role.name}')

    # ── 6. Fix the orphan "Admin" role (empty code) ───────────────────────────
    # This role was manually created with no code; merge users into proper admin
    orphan_roles = Role.objects.filter(code='', name='Admin')
    if orphan_roles.exists() and 'admin' in roles_map:
        proper_admin = roles_map['admin']
        for orphan in orphan_roles:
            # Re-point UserRole records to proper admin
            moved = 0
            for ur in UserRole.objects.filter(role=orphan):
                _, created = UserRole.objects.get_or_create(
                    user_profile=ur.user_profile,
                    role=proper_admin,
                    defaults={'assigned_by': ur.assigned_by}
                )
                if created:
                    moved += 1
            print(f'  [0009] Orphan Admin role: moved {moved} user(s) to proper admin role')
            orphan.delete()
            print(f'  [0009] ✓ Deleted orphan Admin role (empty code)')

    # ── 7. Fix tanzeem.agra's role → super_admin ─────────────────────────────
    TARGET_EMAIL = 'tanzeem.agra@rejlers.ae'
    if 'super_admin' in roles_map:
        super_admin_role = roles_map['super_admin']
        try:
            user = User.objects.get(email=TARGET_EMAIL)
            try:
                profile = UserProfile.objects.get(user=user)
                # Ensure profile has an org
                if profile.organization_id is None:
                    profile.organization = default_org
                    profile.save(update_fields=['organization'])
                _, created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=super_admin_role,
                    defaults={'assigned_by': user}
                )
                if created:
                    print(f'  [0009] ✓ Assigned super_admin to {TARGET_EMAIL}')
                else:
                    print(f'  [0009]   {TARGET_EMAIL} already has super_admin')
            except UserProfile.DoesNotExist:
                profile = UserProfile.objects.create(
                    user=user,
                    organization=default_org,
                    status='active'
                )
                UserRole.objects.create(
                    user_profile=profile,
                    role=super_admin_role,
                    assigned_by=user
                )
                print(f'  [0009] ✓ Created profile + super_admin for {TARGET_EMAIL}')
        except User.DoesNotExist:
            print(f'  [0009]   User {TARGET_EMAIL} not found — skipping')

    print('  [0009] ✅ Full RBAC upgrade complete')


def downgrade_rbac(apps, schema_editor):
    # Downgrade is a no-op — we don't destroy role/module data on rollback
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0008_seed_engineering_modules'),
    ]

    operations = [
        migrations.RunPython(upgrade_rbac, reverse_code=downgrade_rbac),
    ]
