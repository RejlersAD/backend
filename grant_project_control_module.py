"""
Script to grant project_control module access via roles.
This will make Planning Package feature visible in the dashboard.
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule

User = get_user_model()

def grant_project_control_to_roles():
    """Add project_control module to all active roles"""
    print("\n" + "="*70)
    print("GRANT PROJECT_CONTROL MODULE TO ROLES")
    print("="*70)
    
    # Check if project_control module exists
    try:
        module = Module.objects.get(code='project_control')
        print(f"\n✅ Found module: {module.name} ({module.code})")
    except Module.DoesNotExist:
        print("\n❌ project_control module doesn't exist. Creating it...")
        module = Module.objects.create(
            code='project_control',
            name='Project Control',
            description='Project management, planning packages, and control features',
            is_active=True
        )
        print(f"✅ Created module: {module.name}")
    
    # Get all active roles
    roles = Role.objects.filter(is_active=True)
    print(f"\nFound {roles.count()} active roles:")
    
    success_count = 0
    for role in roles:
        print(f"\n   {role.name} (Level {role.level})")
        
        # Check if role already has this module
        if role.has_module_access('project_control'):
            print(f"      ⚠️  Already has project_control module")
        else:
            # Add module to role
            RoleModule.objects.create(role=role, module=module)
            print(f"      ✅ Added project_control module")
            success_count += 1
    
    print(f"\n{'='*70}")
    print(f"✅ Updated {success_count} role(s)")
    print(f"{'='*70}")
    print("\nNext steps:")
    print("1. All users with these roles can now see Planning Package")
    print("2. Refresh browser at http://localhost:5173/dashboard")
    print("3. Clear browser cache if needed (Ctrl+Shift+R)")
    print("4. Look for 'Planning Package' under Project Control")
    print("="*70)
    
    return True


def show_user_modules(username_or_email='admin'):
    """Show what modules a user has access to"""
    print("\n" + "="*70)
    print("USER MODULE ACCESS CHECK")
    print("="*70)
    
    try:
        try:
            user = User.objects.get(email=username_or_email)
        except User.DoesNotExist:
            user = User.objects.get(username=username_or_email)
        
        print(f"\n👤 User: {user.email}")
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   Admin: {user.is_superuser}")
        
        # Get user roles
        if hasattr(user, 'rbac_profile'):
            profile = user.rbac_profile
            roles = profile.roles.all()
            
            print(f"\n   Roles ({roles.count()}):")
            all_modules = set()
            
            for role in roles:
                print(f"      - {role.name}")
                modules = role.modules.filter(is_active=True)
                for mod in modules:
                    all_modules.add(mod.code)
                    print(f"         → {mod.name} ({mod.code})")
            
            print(f"\n   Total unique modules: {len(all_modules)}")
            print(f"   Has project_control: {'project_control' in all_modules}")
        else:
            print("\n   ⚠️  No RBAC profile found")
        
        print("="*70)
        
    except User.DoesNotExist:
        print(f"\n❌ User not found: {username_or_email}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Grant project_control module access')
    parser.add_argument('--grant', action='store_true', help='Grant to all active roles')
    parser.add_argument('--check', default=None, help='Check module access for user')
    
    args = parser.parse_args()
    
    try:
        if args.grant:
            success = grant_project_control_to_roles()
        elif args.check:
            show_user_modules(args.check)
            success = True
        else:
            # Default: grant to all roles
            success = grant_project_control_to_roles()
        
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    """Grant project_control module to a user"""
    print("\n" + "="*70)
    print("GRANT PROJECT_CONTROL MODULE ACCESS")
    print("="*70)
    
    try:
        # Find user
        try:
            user = User.objects.get(email=username_or_email)
            print(f"\n✅ Found user by email: {user.email}")
        except User.DoesNotExist:
            user = User.objects.get(username=username_or_email)
            print(f"\n✅ Found user by username: {user.username}")
        
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   Department: {user.department}")
        print(f"   Is Admin: {user.is_superuser}")
        
        # Get or create UserModules
        user_modules, created = UserModules.objects.get_or_create(user=user)
        
        if created:
            print("\n✅ Created new UserModules record")
        else:
            print("\n✅ Found existing UserModules record")
        
        # Check current modules
        current_modules = user_modules.modules or []
        print(f"\n   Current modules ({len(current_modules)}):")
        for mod in current_modules:
            print(f"      - {mod}")
        
        # Add project_control if not already there
        if 'project_control' in current_modules:
            print("\n⚠️  User already has project_control module")
        else:
            current_modules.append('project_control')
            user_modules.modules = current_modules
            user_modules.save()
            print("\n✅ Added project_control module!")
        
        print(f"\n   Updated modules ({len(user_modules.modules)}):")
        for mod in user_modules.modules:
            print(f"      - {mod}")
        
        print("\n" + "="*70)
        print("✅ SUCCESS - User can now see Planning Package feature!")
        print("="*70)
        print("\nNext steps:")
        print("1. Refresh the browser at http://localhost:5173/dashboard")
        print("2. Clear browser cache/localStorage if needed (Ctrl+Shift+R)")
        print("3. Look for 'Planning Package' under Project Control")
        print("="*70)
        
        return True
        
    except User.DoesNotExist:
        print(f"\n❌ User not found: {username_or_email}")
        print("\nAvailable users:")
        for u in User.objects.filter(is_active=True)[:10]:
            print(f"   - {u.email} ({u.username})")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def grant_to_all_active_users():
    """Grant project_control to all active users"""
    print("\n" + "="*70)
    print("GRANT PROJECT_CONTROL TO ALL ACTIVE USERS")
    print("="*70)
    
    users = User.objects.filter(is_active=True)
    print(f"\nFound {users.count()} active users")
    
    success_count = 0
    for user in users:
        print(f"\nProcessing: {user.email}")
        user_modules, created = UserModules.objects.get_or_create(user=user)
        current_modules = user_modules.modules or []
        
        if 'project_control' not in current_modules:
            current_modules.append('project_control')
            user_modules.modules = current_modules
            user_modules.save()
            print(f"   ✅ Added project_control")
            success_count += 1
        else:
            print(f"   ⚠️  Already has project_control")
    
    print(f"\n✅ Updated {success_count} users")
    print("="*70)
    

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Grant project_control module access')
    parser.add_argument('--user', default='admin', help='Username or email (default: admin)')
    parser.add_argument('--all', action='store_true', help='Grant to all active users')
    
    args = parser.parse_args()
    
    try:
        if args.all:
            success = grant_to_all_active_users()
        else:
            success = grant_project_control_module(args.user)
        
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
