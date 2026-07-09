"""
Diagnostic script to check user's admin module access
Usage: python _diagnose_user_admin_access.py radai@rejlers.ae
"""
import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module, RoleModule, UserRole

User = get_user_model()

def diagnose_user_access(email):
    print(f"\n{'='*80}")
    print(f"ADMIN ACCESS DIAGNOSTIC FOR: {email}")
    print(f"{'='*80}\n")
    
    try:
        user = User.objects.get(email=email)
        print(f"✅ User found: {user.email}")
        print(f"   - is_staff: {user.is_staff}")
        print(f"   - is_superuser: {user.is_superuser}")
        print(f"   - is_active: {user.is_active}")
        print()
    except User.DoesNotExist:
        print(f"❌ User not found: {email}")
        return
    
    # Check RBAC profile
    try:
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        print(f"✅ RBAC Profile found: {profile.id}")
        print(f"   - Status: {profile.status}")
        print(f"   - Organization: {profile.organization}")
        print()
    except UserProfile.DoesNotExist:
        print(f"❌ No RBAC profile found")
        return
    
    # Check roles
    user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    print(f"📋 ROLES ({user_roles.count()}):")
    if user_roles.exists():
        for ur in user_roles:
            primary = "★ PRIMARY" if ur.is_primary else ""
            print(f"   - {ur.role.name} (code: {ur.role.code}, level: {ur.role.level}) {primary}")
    else:
        print(f"   ⚠️  No roles assigned")
    print()
    
    # Check modules via roles
    admin_module_codes = [
        'admin_dashboard',
        'user_mgmt',
        'role_access_mgmt',
        'wrench_integration',
        'ai_champion',
        'enquiry_management'
    ]
    
    print(f"🔍 CHECKING ADMIN MODULE ACCESS:")
    print(f"   Required codes: {', '.join(admin_module_codes)}")
    print()
    
    role_ids = user_roles.values_list('role_id', flat=True)
    role_modules = RoleModule.objects.filter(role_id__in=role_ids).select_related('module')
    
    all_modules = Module.objects.filter(
        rolemodule__role_id__in=role_ids,
        is_active=True
    ).distinct()
    
    print(f"📦 MODULES ACCESSIBLE VIA ROLES ({all_modules.count()}):")
    for module in all_modules:
        is_admin = "✅ ADMIN" if module.code in admin_module_codes else ""
        print(f"   - {module.name} (code: {module.code}) {is_admin}")
    print()
    
    # Check if user has admin access
    has_admin_module = any(m.code in admin_module_codes for m in all_modules)
    has_admin_flags = user.is_staff or user.is_superuser
    has_super_admin_role = any(ur.role.code == 'super_admin' for ur in user_roles)
    
    print(f"🎯 ADMIN ACCESS SUMMARY:")
    print(f"   - Has Admin Flags (is_staff/is_superuser): {has_admin_flags}")
    print(f"   - Has Super Admin Role: {has_super_admin_role}")
    print(f"   - Has Admin Module: {has_admin_module}")
    print(f"   - SHOULD SEE ADMIN SECTION: {has_admin_flags or has_super_admin_role or has_admin_module}")
    print()
    
    # API response simulation
    print(f"📡 SIMULATED API RESPONSE (/rbac/users/me/):")
    modules_data = [{'id': str(m.id), 'code': m.code, 'name': m.name} for m in all_modules]
    print(f"   - modules: {len(modules_data)} items")
    for m in modules_data[:10]:  # Show first 10
        print(f"     • {m['code']}")
    if len(modules_data) > 10:
        print(f"     ... and {len(modules_data) - 10} more")
    print()
    
    # Recommendations
    print(f"💡 RECOMMENDATIONS:")
    if not has_admin_module and not has_admin_flags and not has_super_admin_role:
        print(f"   ⚠️  User has NO admin access. Assign a role with admin modules.")
    elif has_admin_module:
        print(f"   ✅ User has admin modules via roles.")
        print(f"   ✅ Frontend should show admin section (after browser refresh).")
    
    if not user.is_staff and has_admin_module:
        print(f"   💡 Optionally set is_staff=True for Django admin access:")
        print(f"      User.objects.filter(email='{email}').update(is_staff=True)")
    
    print()
    print(f"{'='*80}\n")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python _diagnose_user_admin_access.py <email>")
        print("Example: python _diagnose_user_admin_access.py radai@rejlers.ae")
        sys.exit(1)
    
    email = sys.argv[1]
    diagnose_user_access(email)
