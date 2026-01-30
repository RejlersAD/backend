"""
Smart Super Administrator Access Grant Script
Grants super admin access to multiple users efficiently
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module, UserRole, RoleModule

User = get_user_model()

# Define users to grant super admin access
SUPER_ADMIN_USERS = [
    {
        'email': 'jarmo.suominen@rejlers.ae',
        'defaults': {
            'department': 'Management',
            'job_title': 'Executive Manager'
        }
    },
    {
        'email': 'moghawanmeh@rejlers.ae',
        'defaults': {
            'department': 'Delivery, Digital Solutions',
            'job_title': 'Senior Engineer'
        }
    }
]

def grant_super_admin_access(user_email, profile_defaults):
    """Grant super admin access to a single user"""
    print(f"\n{'=' * 80}")
    print(f"Processing: {user_email}")
    print('=' * 80)
    
    try:
        # Get or create the user
        user, user_created = User.objects.get_or_create(
            email=user_email,
            defaults={
                'username': user_email.split('@')[0],
                'is_staff': True,
                'is_superuser': True,
                'is_active': True
            }
        )
        
        if user_created:
            print(f"✅ Created new user: {user.email}")
            # Set a temporary password for new users
            temp_password = f"Welcome@{user_email.split('@')[0]}123"
            user.set_password(temp_password)
            user.save()
            print(f"   ⚠️  Temporary password set: {temp_password}")
            print(f"   ⚠️  User must change password on first login")
        else:
            print(f"✅ Found existing user: {user.email}")
        
        # Update User flags to ensure super admin status
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()
        
        print(f"   is_staff: {user.is_staff}")
        print(f"   is_superuser: {user.is_superuser}")
        print(f"   is_active: {user.is_active}")
        
        # Get or create UserProfile
        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'status': 'active',
                **profile_defaults
            }
        )
        
        if profile_created:
            print(f"✅ Created new UserProfile")
        else:
            print(f"✅ Found existing UserProfile")
        
        # Ensure profile is active and not deleted
        if profile.is_deleted:
            profile.is_deleted = False
            print(f"   ✅ Restored deleted profile")
        
        profile.status = 'active'
        profile.save()
        print(f"   Status: {profile.status}")
        print(f"   Department: {profile.department}")
        print(f"   Job Title: {profile.job_title}")
        
        # Get Super Administrator role
        super_admin_role = Role.objects.filter(code='super_admin').first()
        
        if not super_admin_role:
            print(f"❌ Super Administrator role not found!")
            return False
        
        print(f"✅ Found Super Administrator role: {super_admin_role.name}")
        
        # Assign role to profile
        user_role, role_created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=super_admin_role,
            defaults={'assigned_by': user}
        )
        
        if role_created:
            print(f"✅ Assigned Super Administrator role")
        else:
            print(f"✅ Super Administrator role already assigned")
        
        # Get accessible modules count
        accessible_modules = profile.get_all_modules()
        print(f"✅ Accessible Modules: {accessible_modules.count()} modules")
        
        return True
        
    except Exception as e:
        print(f"❌ Error processing {user_email}: {e}")
        import traceback
        traceback.print_exc()
        return False


def ensure_super_admin_role_has_all_modules():
    """Ensure Super Administrator role has access to all modules"""
    print(f"\n{'=' * 80}")
    print("Ensuring Super Administrator role has ALL modules")
    print('=' * 80)
    
    super_admin_role = Role.objects.filter(code='super_admin').first()
    
    if not super_admin_role:
        print(f"❌ Super Administrator role not found!")
        return False
    
    all_modules = Module.objects.filter(is_active=True)
    print(f"✅ Found {all_modules.count()} active modules")
    
    modules_added = 0
    for module in all_modules:
        role_module, created = RoleModule.objects.get_or_create(
            role=super_admin_role,
            module=module
        )
        if created:
            modules_added += 1
            print(f"   ✅ Added: {module.name} ({module.code})")
    
    if modules_added > 0:
        print(f"\n✅ Added {modules_added} new modules to Super Administrator role")
    else:
        print(f"\n✅ Super Administrator role already has all modules")
    
    return True


def main():
    """Main execution function"""
    print("=" * 80)
    print("SMART SUPER ADMINISTRATOR ACCESS GRANT")
    print("=" * 80)
    print(f"Target users: {len(SUPER_ADMIN_USERS)}")
    for user_info in SUPER_ADMIN_USERS:
        print(f"  - {user_info['email']}")
    print()
    
    # First, ensure Super Admin role has all modules
    ensure_super_admin_role_has_all_modules()
    
    # Grant access to each user
    success_count = 0
    for user_info in SUPER_ADMIN_USERS:
        if grant_super_admin_access(user_info['email'], user_info['defaults']):
            success_count += 1
    
    # Final summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print('=' * 80)
    print(f"✅ Successfully processed: {success_count}/{len(SUPER_ADMIN_USERS)} users")
    
    if success_count == len(SUPER_ADMIN_USERS):
        print(f"\n🎉 ALL USERS GRANTED SUPER ADMINISTRATOR ACCESS!")
    else:
        print(f"\n⚠️  Some users failed. Please review errors above.")
    
    print(f"\n{'=' * 80}")
    print("VERIFICATION")
    print('=' * 80)
    
    # Verify each user
    for user_info in SUPER_ADMIN_USERS:
        try:
            user = User.objects.get(email=user_info['email'])
            profile = UserProfile.objects.get(user=user)
            roles = list(profile.roles.values_list('name', flat=True))
            modules = profile.get_all_modules().count()
            
            print(f"\n{user_info['email']}:")
            print(f"   Django: is_staff={user.is_staff}, is_superuser={user.is_superuser}")
            print(f"   Profile: status={profile.status}, deleted={profile.is_deleted}")
            print(f"   Roles: {', '.join(roles) if roles else 'NONE'}")
            print(f"   Modules: {modules}")
            
        except Exception as e:
            print(f"\n{user_info['email']}: ❌ Verification failed - {e}")
    
    print(f"\n{'=' * 80}")
    print("✅ SCRIPT COMPLETED")
    print('=' * 80)
    print("\nUsers should logout and login again to see changes.")
    print("New users will need to change their password on first login.")


if __name__ == '__main__':
    main()
