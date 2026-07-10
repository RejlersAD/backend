"""
Production Default Role Diagnostic Script
Run this on Railway to check Default role configuration before/after fix

Usage:
    railway run --service backend python _diagnose_default_role_production.py
    
Or in Railway shell:
    python _diagnose_default_role_production.py
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Role, Module, RoleModule
from apps.rbac.rbac_config import DEFAULT_ROLE_MODULES

print("=" * 80)
print("🔍 DEFAULT ROLE DIAGNOSTIC REPORT (Production)")
print("=" * 80)
print()

# Get Default role
try:
    default_role = Role.objects.get(code='default')
    print(f"✅ Default role found: {default_role.name}")
    print(f"   Code: {default_role.code}")
    print(f"   ID: {default_role.id}")
    print()
except Role.DoesNotExist:
    print("❌ ERROR: Default role not found in database!")
    print("   Run: python manage.py seed_rbac")
    sys.exit(1)

# Get configured modules (what it SHOULD be)
configured_modules = set(DEFAULT_ROLE_MODULES)
print(f"📋 EXPECTED Configuration ({len(configured_modules)} modules):")
print(f"   From: apps/rbac/rbac_config.py → DEFAULT_ROLE_MODULES")
print()

# Categorize configured modules
engineering_modules = [
    'pid_analysis', 'pid_verification', 'pid_equipment_list', 'pid_line_list',
    'pfd_quality', 'process_datasheet', 'piping_datasheet', 'piping_pms',
    'piping_critical_line_list', 'electrical_datasheet', 'electrical_sld',
    'civil_datasheet', 'mechanical_datasheet'
]
common_modules = ['crs_documents', 'pfd_to_pid', 'designiq', 'data_mining', 'hr_self_service']
metadata_modules = ['non_teff_metadata', 'spec_customization']

print("   ✅ Dashboard (always accessible - no module required)")
print(f"   ✅ 1. Engineering: {len([m for m in configured_modules if m in engineering_modules])} modules")
for m in sorted(configured_modules):
    if m in engineering_modules:
        print(f"      - {m}")
print()
print(f"   ✅ 2. COMMON: {len([m for m in configured_modules if m in common_modules])} modules")
for m in sorted(configured_modules):
    if m in common_modules:
        print(f"      - {m}")
print()

# Get current database state
current_role_modules = RoleModule.objects.filter(role=default_role)
current_module_codes = set(rm.module.code for rm in current_role_modules)

print(f"📊 ACTUAL Database State ({len(current_module_codes)} modules):")
print(f"   From: Database → RoleModule table")
print()

# Check for correct modules
correct_modules = current_module_codes & configured_modules
extra_modules = current_module_codes - configured_modules
missing_modules = configured_modules - current_module_codes

if correct_modules:
    print(f"   ✅ Correct modules ({len(correct_modules)}):")
    for m in sorted(correct_modules):
        print(f"      ✓ {m}")
    print()

if extra_modules:
    print(f"   ❌ EXTRA modules ({len(extra_modules)}) - SHOULD BE REMOVED:")
    for m in sorted(extra_modules):
        # Highlight problematic modules
        if m in ['finance', 'procurement', 'qhse', 'admin']:
            print(f"      🚨 {m}  ← CRITICAL: Default users should NOT have this!")
        else:
            print(f"      ⚠️ {m}")
    print()

if missing_modules:
    print(f"   ❌ MISSING modules ({len(missing_modules)}) - SHOULD BE ADDED:")
    for m in sorted(missing_modules):
        print(f"      ➕ {m}")
    print()

# Final verdict
print("=" * 80)
print("📋 DIAGNOSTIC SUMMARY")
print("=" * 80)

if extra_modules or missing_modules:
    print("❌ DEFAULT ROLE IS OUT OF SYNC")
    print()
    print(f"   Modules to ADD:    {len(missing_modules)}")
    print(f"   Modules to REMOVE: {len(extra_modules)}")
    print()
    
    # Show critical issues
    critical_extra = [m for m in extra_modules if m in ['finance', 'procurement', 'qhse', 'admin']]
    if critical_extra:
        print(f"   🚨 CRITICAL: Default users can access {len(critical_extra)} restricted sections:")
        for m in critical_extra:
            section_map = {
                'finance': '3. Finance',
                'procurement': '6. Procurement',
                'qhse': '7. QHSE',
                'admin': '9. Admin'
            }
            print(f"      - {section_map.get(m, m)}")
        print()
    
    print("🔧 TO FIX:")
    print("   Run: python manage.py sync_default_role")
    print()
    print("   OR preview changes first:")
    print("   Run: python manage.py sync_default_role --dry-run")
    
else:
    print("✅ DEFAULT ROLE IS CORRECTLY CONFIGURED")
    print()
    print(f"   Total modules: {len(current_module_codes)}")
    print(f"   All {len(configured_modules)} expected modules are assigned")
    print(f"   No extra modules found")
    print()
    print("   Default role users will see:")
    print("     ✅ Dashboard")
    print("     ✅ 1. Engineering (all sub-sections)")
    print("     ✅ 2. COMMON (CRS, PFD, DesignIQ, Data Mining, My Profile)")
    print("     ❌ Finance, Procurement, QHSE, Admin (requires explicit role)")

print("=" * 80)

# Show what Default role users will see in sidebar
print()
print("🖥️  SIDEBAR VISIBILITY FOR DEFAULT ROLE USERS:")
print("=" * 80)

sections = {
    'Dashboard': True,  # Always visible
    '1. Engineering': any(m in current_module_codes for m in engineering_modules),
    '2. COMMON': any(m in current_module_codes for m in common_modules),
    '3. Finance': 'finance' in current_module_codes,
    '4. Human Resource': any(m in current_module_codes for m in ['hr_management', 'payroll', 'timesheet', 'hr_onboarding']),
    '5. Timesheet': 'timesheet' in current_module_codes,
    '6. Procurement': 'procurement' in current_module_codes,
    '7. QHSE': any(m in current_module_codes for m in ['qhse', 'qhse_detailed', 'qhse_project_qhse']),
    '8. AI/ML Sales': 'sales' in current_module_codes,
    '9. Admin': 'admin' in current_module_codes,
}

for section, visible in sections.items():
    if visible:
        # Check if it should be visible
        should_be_visible = section in ['Dashboard', '1. Engineering', '2. COMMON']
        if should_be_visible:
            print(f"   ✅ {section}  (correct)")
        else:
            print(f"   🚨 {section}  (WRONG - should NOT be visible!)")
    else:
        # Check if it should be hidden
        should_be_hidden = section not in ['Dashboard', '1. Engineering', '2. COMMON']
        if should_be_hidden:
            print(f"   ❌ {section}  (correct - hidden)")
        else:
            print(f"   ⚠️ {section}  (WRONG - should be visible!)")

print("=" * 80)
print()

# Test with an actual Default role user (if exists)
from apps.users.models import User
default_users = User.objects.filter(rbac_profile__roles__code='default').distinct()[:3]

if default_users:
    print("👥 SAMPLE DEFAULT ROLE USERS:")
    print("=" * 80)
    for user in default_users:
        print(f"   Email: {user.email}")
        print(f"   Name: {user.first_name} {user.last_name}")
        try:
            profile = user.rbac_profile
            all_modules = profile.get_all_modules()
            print(f"   Modules: {len(all_modules)}")
            
            # Check if user has extra roles
            user_roles = profile.roles.all()
            if len(user_roles) > 1 or (len(user_roles) == 1 and user_roles[0].code != 'default'):
                print(f"   ⚠️ Warning: User has {len(user_roles)} role(s):")
                for role in user_roles:
                    print(f"      - {role.name} ({role.code})")
                print(f"   Note: User sees UNION of all role modules")
            else:
                print(f"   ✅ Has only Default role")
        except Exception as e:
            print(f"   ❌ Error: {e}")
        print()
else:
    print("ℹ️  No users with Default role found in database")
    print()

print("=" * 80)
print("🎓 NEXT STEPS:")
print("=" * 80)

if extra_modules or missing_modules:
    print()
    print("1. Review the changes above")
    print("2. Run: python manage.py sync_default_role --dry-run  (preview)")
    print("3. Run: python manage.py sync_default_role  (apply)")
    print("4. Re-run this diagnostic to verify: python _diagnose_default_role_production.py")
    print()
else:
    print()
    print("✅ No action needed - Default role is correctly configured!")
    print()
    print("Verification:")
    print("1. Login to https://www.radai.ae as a Default role user")
    print("2. Verify sidebar shows only: Dashboard, Engineering, COMMON")
    print("3. Verify no access to: Finance, Procurement, QHSE, Admin")
    print()

print("=" * 80)
