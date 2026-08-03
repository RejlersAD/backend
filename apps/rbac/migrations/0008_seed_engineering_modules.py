"""
Data Migration: Seed all engineering & discipline modules
=========================================================
This migration creates all engineering modules that were missing from the
initial seed_rbac.py run.  It is idempotent — safe to re-run.

Modules created (if not already present):
  process_datasheet, electrical_datasheet, electrical_sld,
  instrument_datasheet, instrument_index, mechanical_datasheet,
  civil_datasheet, piping_datasheet, piping_pms, designiq,
  pfd_to_pid, digitization_datasheet, spec_customization

SOFT-CODED: edit ENGINEERING_MODULES below to add/remove modules
without changing the migration itself.
"""
from django.db import migrations

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED catalogue — single source of truth for this migration
# ─────────────────────────────────────────────────────────────────────────────
ENGINEERING_MODULES = [
    # Already seeded by seed_rbac — included here for idempotence
    {'code': 'pid_analysis',          'name': 'P&ID Analysis',                'order': 1,  'icon': 'FileText',   'description': 'P&ID document analysis and processing'},
    {'code': 'pfd_to_pid',            'name': 'PFD to P&ID Converter',        'order': 2,  'icon': 'RefreshCw',  'description': 'AI-powered conversion of PFD to P&ID drawings'},
    {'code': 'crs_documents',         'name': 'CRS Document Management',      'order': 3,  'icon': 'FolderOpen', 'description': 'Upload and manage CRS documents with AI analysis'},
    {'code': 'designiq',              'name': 'DesignIQ',                     'order': 4,  'icon': 'Cpu',        'description': 'AI-powered design intelligence and PFD verification'},
    # Engineering discipline modules — NEW (missing from prod DB)
    {'code': 'process_datasheet',     'name': 'Process Datasheet',            'order': 10, 'icon': 'FileText',   'description': 'Process equipment datasheets — MOV, SDV, pumps, pressure instruments'},
    {'code': 'electrical_datasheet',  'name': 'Electrical Datasheet',         'order': 11, 'icon': 'Zap',        'description': 'Electrical equipment and SLD-based datasheet generation'},
    {'code': 'electrical_sld',        'name': 'Electrical SLD',               'order': 12, 'icon': 'Zap',        'description': 'Single Line Diagram analysis and tagging'},
    {'code': 'instrument_datasheet',  'name': 'Instrument Datasheet',         'order': 13, 'icon': 'Activity',   'description': 'Instrument equipment datasheets and tag lists'},
    {'code': 'instrument_index',      'name': 'Instrument Index',             'order': 14, 'icon': 'List',       'description': 'AI extraction of instrument index from P&ID drawings'},
    {'code': 'mechanical_datasheet',  'name': 'Mechanical Datasheet',         'order': 15, 'icon': 'Tool',       'description': 'Mechanical equipment datasheets and inspection records'},
    {'code': 'civil_datasheet',       'name': 'Civil Datasheet',              'order': 16, 'icon': 'Home',       'description': 'Civil and structural engineering datasheets'},
    {'code': 'piping_datasheet',      'name': 'Piping Datasheet',             'order': 17, 'icon': 'GitBranch',  'description': 'Piping material specifications and critical line list'},
    {'code': 'piping_pms',            'name': 'Piping Material Specification', 'order': 18, 'icon': 'Database',  'description': 'Piping material specification management'},
    {'code': 'digitization_datasheet','name': 'Digitization Datasheet',       'order': 19, 'icon': 'Scan',       'description': 'AI-powered digitization of legacy datasheets'},
    {'code': 'spec_customization',    'name': 'Spec Customization',           'order': 20, 'icon': 'Settings',   'description': 'Engineering specification customization tools'},
]


def seed_engineering_modules(apps, schema_editor):
    """Create all engineering modules (idempotent) and assign to existing roles."""
    Module = apps.get_model('rbac', 'Module')
    Role = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias = schema_editor.connection.alias

    # ── 1. Create / update modules ─────────────────────────────────────
    created_count = 0
    module_objects = {}
    for m in ENGINEERING_MODULES:
        obj, created = Module.objects.using(db_alias).get_or_create(
            code=m['code'],
            defaults={
                'name': m['name'],
                'description': m['description'],
                'icon': m['icon'],
                'order': m['order'],
                'is_active': True,
            }
        )
        module_objects[m['code']] = obj
        if created:
            created_count += 1
    print(f'\n  [0008] Seeded {created_count} new engineering modules.')

    # ── 2. Assign all modules to super_admin role (if it exists) ──────
    try:
        super_admin_role = Role.objects.using(db_alias).get(code='super_admin')
        assigned = 0
        for mod in module_objects.values():
            _, c = RoleModule.objects.using(db_alias).get_or_create(
                role=super_admin_role, module=mod
            )
            if c:
                assigned += 1
        print(f'  [0008] Assigned {assigned} modules to super_admin role.')
    except Role.DoesNotExist:
        print('  [0008] super_admin role not found — skipping role assignment.')

    # ── 3. Assign process_datasheet to process_engineer role ──────────
    ROLE_MODULE_MAP = {
        'process_engineer':   ['process_datasheet', 'pid_analysis', 'pfd_to_pid', 'designiq'],
        'electrical_engineer':['electrical_datasheet', 'electrical_sld', 'pid_analysis'],
        'instrument_engineer':['instrument_datasheet', 'instrument_index', 'pid_analysis'],
        'mechanical_engineer':['mechanical_datasheet', 'pid_analysis'],
        'civil_engineer':     ['civil_datasheet'],
        'piping_engineer':    ['piping_datasheet', 'piping_pms', 'pid_analysis'],
        'admin':              list(module_objects.keys()),
    }
    for role_code, mod_codes in ROLE_MODULE_MAP.items():
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
            for code in mod_codes:
                mod = module_objects.get(code)
                if mod:
                    RoleModule.objects.using(db_alias).get_or_create(role=role, module=mod)
        except Role.DoesNotExist:
            pass  # Role doesn't exist yet — skipped silently


def reverse_seed(apps, schema_editor):
    """Reverse migration: remove only the NEW modules added here (not pre-existing ones)."""
    Module = apps.get_model('rbac', 'Module')
    db_alias = schema_editor.connection.alias
    new_codes = {m['code'] for m in ENGINEERING_MODULES} - {
        'pid_analysis', 'pfd_to_pid', 'crs_documents', 'designiq'
    }
    Module.objects.using(db_alias).filter(code__in=new_codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0007_alter_userprofile_profile_photo_storage'),
    ]

    operations = [
        migrations.RunPython(seed_engineering_modules, reverse_code=reverse_seed),
    ]
