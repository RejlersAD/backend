"""
Grant Super Administrator Access to tanzeem.agra@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module

User = get_user_model()

print("=" * 100)
print("GRANTING SUPER ADMINISTRATOR ACCESS")
print("=" * 100)

try:
    # Get the user
    user = User.objects.get(email='tanzeem.agra@rejlers.ae')
    
    print(f"\n✅ Found user: {user.email}")
    print(f"   Current is_staff: {user.is_staff}")
    print(f"   Current is_superuser: {user.is_superuser}")
    print(f"   Current is_active: {user.is_active}")
    
    # Update User flags
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.save()
    
    print(f"\n✅ Updated Django User flags:")
    print(f"   is_staff: {user.is_staff}")
    print(f"   is_superuser: {user.is_superuser}")
    print(f"   is_active: {user.is_active}")
    
    # Get or create UserProfile
    profile, created = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'status': 'active',
            'department': 'Delivery, Digital Solutions',
            'job_title': 'Digital Transformation Lead'
        }
    )
    
    if created:
        print(f"\n✅ Created new UserProfile")
    else:
        print(f"\n✅ Found existing UserProfile")
    
    # Ensure profile is active
    if profile.is_deleted:
        profile.is_deleted = False
        profile.save()
        print(f"   ✅ Restored deleted profile")
    
    profile.status = 'active'
    profile.save()
    print(f"   Status: {profile.status}")
    
    # Get Super Administrator role
    super_admin_role = Role.objects.filter(code='super_admin').first()
    
    if super_admin_role:
        print(f"\n✅ Found Super Administrator role: {super_admin_role.name}")
        
        # Assign role to profile
        from apps.rbac.models import UserRole
        user_role, created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=super_admin_role,
            defaults={'assigned_by': user}
        )
        
        if created:
            print(f"   ✅ Assigned Super Administrator role")
        else:
            print(f"   ✅ Super Administrator role already assigned")
    else:
        print(f"\n❌ Super Administrator role not found!")
        print(f"   Available roles: {list(Role.objects.values_list('code', 'name'))}")
    
    # Assign ALL modules to Super Administrator role
    if super_admin_role:
        from apps.rbac.models import RoleModule
        all_modules = Module.objects.filter(is_active=True)
        
        print(f"\n✅ Assigning all modules to Super Administrator role ({all_modules.count()} modules):")
        for module in all_modules:
            RoleModule.objects.get_or_create(
                role=super_admin_role,
                module=module
            )
            print(f"   - {module.name} ({module.code})")
        
        print(f"\n✅ All modules assigned to Super Administrator role")
    
    # Verify final state
    print(f"\n{'=' * 100}")
    print("VERIFICATION - FINAL STATE")
    print("=" * 100)
    
    # Re-fetch user and profile
    user.refresh_from_db()
    profile.refresh_from_db()
    
    print(f"\nDjango User:")
    print(f"   Email: {user.email}")
    print(f"   is_staff: {user.is_staff}")
    print(f"   is_superuser: {user.is_superuser}")
    print(f"   is_active: {user.is_active}")
    
    print(f"\nUserProfile:")
    print(f"   ID: {profile.id}")
    print(f"   Status: {profile.status}")
    print(f"   is_deleted: {profile.is_deleted}")
    print(f"   Department: {profile.department}")
    print(f"   Job Title: {profile.job_title}")
    
    roles = list(profile.roles.values_list('name', flat=True))
    print(f"   Roles: {', '.join(roles) if roles else 'NO ROLES'}")
    
    accessible_modules = profile.get_all_modules()
    print(f"   Accessible Modules: {accessible_modules.count()} modules")
    for mod in accessible_modules:
        print(f"      - {mod.name}")
    
    print(f"\n{'=' * 100}")
    print("✅ SUPER ADMINISTRATOR ACCESS GRANTED SUCCESSFULLY!")
    print("=" * 100)
    print(f"\nPlease logout and login again to see the changes.")
    
except User.DoesNotExist:
    print(f"\n❌ User not found: tanzeem.agra@rejlers.ae")
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
