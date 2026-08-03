"""
COMPLETE ICT ADMIN SETUP - Run in Railway Backend Shell
========================================================
Grants full admin section access to ICT user radai@rejlers.ae

This script:
1. Ensures all 6 admin modules exist
2. Grants modules to admin role
3. Verifies user has admin role
4. Clears user cache

Usage in Railway Shell:
    exec(open('railway_setup_ict_admin.py').read())
"""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rad_ai.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from apps.rbac.models import UserProfile, Role, Module, RoleModule, UserRole

User = get_user_model()

print("\n" + "="*80)
print("🔧 COMPLETE ICT ADMIN SETUP - SOFT-CODED RBAC")
print("="*80 + "\n")

# ===== SOFT-CODED CONFIGURATION =====
ICT_USER_EMAIL = 'radai@rejlers.ae'
ICT_ROLE_CODE = 'admin'  # Level 2 - Limited admin access
ICT_DEPARTMENT = 'ICT'

# Admin section modules (soft-coded from rbac_config.py)
ADMIN_MODULES = [
    {
        'code': 'admin_dashboard',
        'name': 'Admin Dashboard',
        'description': 'System overview & analytics dashboard',
        'icon': 'ChartBar',
        'order': 50,
    },
    {
        'code': 'user_mgmt',
        'name': 'User Management',
        'description': 'Manage users, roles, and permissions',
        'icon': 'Users',
        'order': 51,
    },
    {
        'code': 'role_access_mgmt',
        'name': 'Role & Access Management',
        'description': 'Roles, module permissions & access approvals',
        'icon': 'ShieldCheck',
        'order': 52,
    },
    {
        'code': 'wrench_integration',
        'name': 'Wrench Integration',
        'description': 'Wrench Smart Project Platform integration',
        'icon': 'Wrench',
        'order': 53,
    },
    {
        'code': 'ai_champion',
        'name': 'AI Champion',
        'description': 'AI Champion leaderboard and engagement analytics',
        'icon': 'Trophy',
        'order': 54,
    },
    {
        'code': 'enquiry_management',
        'name': 'Enquiry Management',
        'description': 'Customer enquiries from public contact form',
        'icon': 'Envelope',
        'order': 55,
    },
]

try:
    # ===== STEP 1: GET USER AND ROLE =====
    print(f"📧 User: {ICT_USER_EMAIL}")
    user = User.objects.get(email=ICT_USER_EMAIL)
    profile = UserProfile.objects.get(user=user, is_deleted=False)
    print(f"👤 Profile: {profile.full_name or user.email}")
    
    admin_role = Role.objects.get(code=ICT_ROLE_CODE)
    print(f"📋 Target Role: {admin_role.name} ({admin_role.code}) - Level {admin_role.level}\n")
    
    # ===== STEP 2: ENSURE USER HAS ADMIN ROLE =====
    print("🔐 Step 1: Verify User Role Assignment")
    print("-" * 80)
    
    user_role, role_created = UserRole.objects.get_or_create(
        user_profile=profile,
        role=admin_role,
        defaults={
            'is_primary': True,
            'granted_by': user,
        }
    )
    
    if role_created:
        print(f"✅ Assigned {admin_role.name} role to {ICT_USER_EMAIL}")
    else:
        print(f"⚠️  User already has {admin_role.name} role")
    
    # Update department
    if profile.department != ICT_DEPARTMENT:
        profile.department = ICT_DEPARTMENT
        profile.save()
        print(f"✅ Updated department to {ICT_DEPARTMENT}")
    
    # ===== STEP 3: CREATE/UPDATE ADMIN MODULES =====
    print(f"\n📦 Step 2: Ensure Admin Modules Exist ({len(ADMIN_MODULES)} modules)")
    print("-" * 80)
    
    module_objects = []
    for mod_config in ADMIN_MODULES:
        module, created = Module.objects.get_or_create(
            code=mod_config['code'],
            defaults={
                'name': mod_config['name'],
                'description': mod_config['description'],
                'icon': mod_config['icon'],
                'order': mod_config['order'],
                'is_active': True,
            }
        )
        
        if not created:
            # Update existing module
            module.name = mod_config['name']
            module.description = mod_config['description']
            module.icon = mod_config['icon']
            module.order = mod_config['order']
            module.is_active = True
            module.save()
        
        module_objects.append(module)
        status = "Created" if created else "Updated"
        print(f"  {'✅' if created else '🔄'} {status:7} {mod_config['code']:25} {mod_config['name']}")
    
    # ===== STEP 4: GRANT MODULES TO ADMIN ROLE =====
    print(f"\n🔑 Step 3: Grant Modules to {admin_role.name} Role")
    print("-" * 80)
    
    granted_count = 0
    already_granted_count = 0
    
    for module in module_objects:
        role_module, created = RoleModule.objects.get_or_create(
            role=admin_role,
            module=module,
        )
        
        if created:
            print(f"  ✅ Granted: {module.code:25} → {admin_role.code}")
            granted_count += 1
        else:
            print(f"  ⚠️  Already: {module.code:25} → {admin_role.code}")
            already_granted_count += 1
    
    # ===== STEP 5: CLEAR USER CACHE =====
    print(f"\n🔄 Step 4: Clear User Cache")
    print("-" * 80)
    
    cache.delete(f'user_modules_{profile.id}')
    cache.delete(f'user_permissions_{profile.id}')
    print(f"✅ Cleared module/permission cache for {ICT_USER_EMAIL}")
    
    # ===== STEP 6: VERIFY FINAL STATE =====
    print(f"\n📋 Step 5: Verification")
    print("-" * 80)
    
    # User roles
    print(f"\n👤 User Roles:")
    for ur in UserRole.objects.filter(user_profile=profile).select_related('role'):
        print(f"  • {ur.role.name:30} ({ur.role.code:15}) Level {ur.role.level}")
    
    # User flags
    print(f"\n🚩 Django Flags:")
    print(f"  • is_staff: {user.is_staff}")
    print(f"  • is_superuser: {user.is_superuser}")
    print(f"  • Department: {profile.department}")
    
    # Module access
    accessible_modules = profile.get_all_modules()
    print(f"\n📦 Accessible Modules ({accessible_modules.count()} total):")
    
    admin_module_codes = [m['code'] for m in ADMIN_MODULES]
    for module in accessible_modules.order_by('order')[:20]:
        is_admin = "✅ ADMIN" if module.code in admin_module_codes else ""
        print(f"  • {module.code:25} {module.name:40} {is_admin}")
    
    # ===== SUMMARY =====
    print(f"\n{'='*80}")
    print(f"✅ ICT ADMIN SETUP COMPLETE")
    print(f"{'='*80}")
    print(f"User:                   {ICT_USER_EMAIL}")
    print(f"Role:                   {admin_role.name} (level {admin_role.level})")
    print(f"Department:             {ICT_DEPARTMENT}")
    print(f"Modules granted:        {granted_count}")
    print(f"Modules already had:    {already_granted_count}")
    print(f"Total admin modules:    {len(ADMIN_MODULES)}")
    print(f"Total accessible:       {accessible_modules.count()}")
    print(f"{'='*80}")
    
    print(f"\n⚠️  USER ACTION REQUIRED:")
    print(f"{'='*80}")
    print(f"1. User must LOG OUT from https://www.radai.ae")
    print(f"2. Clear browser cache (Ctrl+Shift+Delete)")
    print(f"3. Log back in")
    print(f"4. Access admin features:")
    print(f"   • https://www.radai.ae/admin/dashboard")
    print(f"   • https://www.radai.ae/admin/users")
    print(f"   • https://www.radai.ae/admin/roles")
    print(f"   • https://www.radai.ae/admin/wrench")
    print(f"   • https://www.radai.ae/admin/ai-champion")
    print(f"   • https://www.radai.ae/admin/enquiries")
    print(f"{'='*80}\n")
    
except User.DoesNotExist:
    print(f"❌ ERROR: User not found: {ICT_USER_EMAIL}")
    print(f"Please create the user first or check the email address.")
except Role.DoesNotExist:
    print(f"❌ ERROR: Role not found: {ICT_ROLE_CODE}")
    print(f"Run: python manage.py seed_rbac")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
