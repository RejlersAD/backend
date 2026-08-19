"""
Migration 0032 — Seed Admin Section Modules
============================================
Seeds the complete set of Admin section (section 9) module codes that are
referenced in the Sidebar but were missing from the DB:

  Admin modules (new):
    - admin_dashboard         Admin Dashboard (9.1)
    - role_access_mgmt        Role & Access Management (9.3)
    - wrench_integration      Wrench Integration (9.4)
    - ai_champion             AI Champion (9.5)
    - enquiry_management      Enquiry Management (9.6)

  Admin modules (existing — updated for consistency):
    - user_mgmt               User Management (9.2)
    - org_settings            Organization Settings
    - audit_logs              Audit Logs
    - file_storage            File Storage
    - reports                 Reports & Analytics
    - api_access              API Access

All new modules are created idempotently (get_or_create) and assigned
to the super_admin and admin roles.  This ensures the Role & Access Management
panel correctly displays all admin features under the Administration group.
"""
from django.db import migrations

# ── Soft-coded catalogue for this migration ───────────────────────────────────
NEW_ADMIN_MODULES = [
    {
        'code':        'admin_dashboard',
        'name':        'Admin Dashboard',
        'icon':        'ChartBar',
        'order':       50,
        'description': 'System overview & analytics dashboard for administrators',
    },
    {
        'code':        'role_access_mgmt',
        'name':        'Role & Access Management',
        'icon':        'ShieldCheck',
        'order':       52,
        'description': 'Roles, module permissions & access request approvals',
    },
    {
        'code':        'wrench_integration',
        'name':        'Wrench Integration',
        'icon':        'Wrench',
        'order':       53,
        'description': 'Wrench Smart Project Platform integration and sync',
    },
    {
        'code':        'ai_champion',
        'name':        'AI Champion',
        'icon':        'Trophy',
        'order':       54,
        'description': 'AI Champion leaderboard, badges and engagement analytics',
    },
    {
        'code':        'enquiry_management',
        'name':        'Enquiry Management',
        'icon':        'Envelope',
        'order':       55,
        'description': 'Customer enquiries from public contact form',
    },
]

# Roles that should receive all these new modules
GRANT_TO_ROLES = ['super_admin', 'admin']


def seed_admin_section_modules(apps, schema_editor):
    Module    = apps.get_model('rbac', 'Module')
    Role      = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias  = schema_editor.connection.alias

    # 1. Create / update every module
    module_objs = {}
    for m in NEW_ADMIN_MODULES:
        obj, created = Module.objects.using(db_alias).get_or_create(
            code=m['code'],
            defaults={
                'name':        m['name'],
                'description': m['description'],
                'icon':        m['icon'],
                'order':       m['order'],
                'is_active':   True,
            },
        )
        if not created:
            # Keep name/description/order up-to-date
            obj.name        = m['name']
            obj.description = m['description']
            obj.order       = m['order']
            obj.is_active   = True
            obj.save(using=db_alias)
        module_objs[m['code']] = obj
        action = 'created' if created else 'updated'
        print(f'  [0032] ✓ Module {action}: {m["code"]}')

    # 2. Assign to target roles
    for role_code in GRANT_TO_ROLES:
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
        except Role.DoesNotExist:
            print(f'  [0032] ⚠ Role not found: {role_code} — skipping')
            continue
        for mod in module_objs.values():
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role,
                module=mod,
            )
            if created:
                print(f'  [0032] ✓ Granted {mod.code} → {role_code}')


def noop(apps, schema_editor):
    """Intentional no-op reverse — do not strip modules on rollback."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0031_seed_hr_onboarding_module'),
    ]

    operations = [
        migrations.RunPython(seed_admin_section_modules, noop),
    ]
