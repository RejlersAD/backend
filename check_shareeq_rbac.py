"""Check shareeq user and RBAC configuration"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password

User = get_user_model()

print("=" * 70)
print("CHECKING USER: shareeq@rejlers.ae")
print("=" * 70)

# Check if user exists
try:
    user = User.objects.get(email='shareeq@rejlers.ae')
    print(f"\n✅ User found:")
    print(f"   ID: {user.id}")
    print(f"   Username: {user.username}")
    print(f"   Email: {user.email}")
    print(f"   Full Name: {user.first_name} {user.last_name}")
    print(f"   Is Active: {user.is_active}")
    print(f"   Is Staff: {user.is_staff}")
    print(f"   Is Superuser: {user.is_superuser}")
    
    # Test password
    print(f"\n🔐 Password Check:")
    password_correct = check_password('Shareeq@123', user.password)
    if password_correct:
        print(f"   ✅ Password 'Shareeq@123': CORRECT")
    else:
        print(f"   ❌ Password 'Shareeq@123': INCORRECT")
        # Try alternative password
        alt_password_correct = check_password('shareeq123', user.password)
        if alt_password_correct:
            print(f"   ✅ Alternative password 'shareeq123': CORRECT")
        else:
            print(f"   ❌ Alternative password 'shareeq123': INCORRECT")
    
    # Check RBAC configuration
    print(f"\n👤 RBAC Profile:")
    try:
        from apps.rbac.models import UserProfile, Role, Module
        
        profile = UserProfile.objects.filter(user=user).first()
        if profile:
            print(f"   ✅ UserProfile exists")
            print(f"   Profile ID: {profile.id}")
            print(f"   Organization: {profile.organization or 'N/A'}")
            print(f"   Department: {profile.department or 'N/A'}")
            
            # Get roles
            roles = profile.roles.all()
            print(f"\n   📋 Roles ({roles.count()}):")
            for role in roles:
                print(f"      - {role.name} (ID: {role.id})")
                print(f"        Description: {role.description}")
                print(f"        Is Active: {role.is_active}")
                
                # Get modules for this role
                modules = role.modules.all()
                print(f"        Modules ({modules.count()}):")
                for module in modules:
                    print(f"          • {module.name} ({module.code})")
                    print(f"            Description: {module.description}")
                    print(f"            Is Active: {module.is_active}")
            
            # Get all accessible modules (from all roles)
            all_modules = Module.objects.filter(
                roles__userprofile=profile,
                is_active=True
            ).distinct()
            
            print(f"\n   🔓 Total Accessible Modules: {all_modules.count()}")
            for module in all_modules:
                print(f"      • {module.name} ({module.code})")
        else:
            print(f"   ❌ No UserProfile found - RBAC not configured")
            print(f"   Creating UserProfile...")
            
            # Create profile
            profile = UserProfile.objects.create(
                user=user,
                organization='Rejlers',
                department='Engineering'
            )
            print(f"   ✅ UserProfile created (ID: {profile.id})")
            
            # Assign default role with modules
            try:
                # Try to find PFD Converter role or create it
                pfd_role = Role.objects.filter(name__icontains='PFD').first()
                if not pfd_role:
                    # Create a role with PFD and PID modules
                    pfd_role = Role.objects.create(
                        name='PFD & PID User',
                        description='User with access to PFD and PID modules'
                    )
                    
                    # Get or create modules
                    pfd_module, _ = Module.objects.get_or_create(
                        code='PFD',
                        defaults={
                            'name': 'PFD to P&ID Converter',
                            'description': 'Convert PFD diagrams to P&ID'
                        }
                    )
                    pid_module, _ = Module.objects.get_or_create(
                        code='PID',
                        defaults={
                            'name': 'P&ID Analysis',
                            'description': 'Analyze P&ID drawings'
                        }
                    )
                    
                    pfd_role.modules.add(pfd_module, pid_module)
                
                profile.roles.add(pfd_role)
                print(f"   ✅ Assigned role: {pfd_role.name}")
                print(f"   Modules: {', '.join([m.code for m in pfd_role.modules.all()])}")
                
            except Exception as e:
                print(f"   ❌ Failed to assign role: {e}")
    
    except ImportError:
        print(f"   ❌ RBAC app not available")
    
    # Test authentication
    print(f"\n🔑 Authentication Test:")
    from django.contrib.auth import authenticate
    
    auth_user = authenticate(email='shareeq@rejlers.ae', password='Shareeq@123')
    if auth_user:
        print(f"   ✅ Authentication with 'Shareeq@123': SUCCESS")
    else:
        print(f"   ❌ Authentication with 'Shareeq@123': FAILED")
        auth_user = authenticate(email='shareeq@rejlers.ae', password='shareeq123')
        if auth_user:
            print(f"   ✅ Authentication with 'shareeq123': SUCCESS")
        else:
            print(f"   ❌ Authentication with 'shareeq123': FAILED")

except User.DoesNotExist:
    print(f"\n❌ User 'shareeq@rejlers.ae' not found in database")
    print(f"\nAvailable users:")
    for u in User.objects.all():
        print(f"   - {u.email} ({u.username})")

except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
