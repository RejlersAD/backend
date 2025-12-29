"""
Check and assign PFD module access to shareeq@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import Module, UserRole, Role

def check_and_assign_access():
    # Find the user
    try:
        user = User.objects.get(email='shareeq@rejlers.ae')
        print(f"✅ Found user: {user.email}")
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   Active: {user.is_active}")
        print(f"   Staff: {user.is_staff}")
        print(f"   Superuser: {user.is_superuser}")
    except User.DoesNotExist:
        print("❌ User shareeq@rejlers.ae not found!")
        return

    # Check current modules
    current_modules = list(user.modules.all())
    print(f"\n📦 Current Modules ({len(current_modules)}):")
    for module in current_modules:
        print(f"   - {module.code}: {module.name}")

    # Check roles
    user_roles = UserRole.objects.filter(user=user)
    print(f"\n👤 Current Roles ({user_roles.count()}):")
    for ur in user_roles:
        print(f"   - {ur.role.name} (Code: {ur.role.code})")
        role_modules = ur.role.modules.all()
        print(f"     Modules via role: {[m.code for m in role_modules]}")

    # Find PFD module
    try:
        pfd_module = Module.objects.get(code='PFD')
        print(f"\n🎯 PFD Module found: {pfd_module.name}")
        
        # Check if user already has PFD access
        if user.modules.filter(code='PFD').exists():
            print("   ✅ User already has direct PFD access")
        else:
            print("   ❌ User does NOT have direct PFD access")
            print("   🔧 Adding PFD module access...")
            user.modules.add(pfd_module)
            user.save()
            print("   ✅ PFD access granted!")
            
    except Module.DoesNotExist:
        print("❌ PFD module not found in database!")
        print("\n📋 Available modules:")
        for module in Module.objects.all():
            print(f"   - {module.code}: {module.name}")

    # Also check for PID module
    try:
        pid_module = Module.objects.get(code='PID')
        if not user.modules.filter(code='PID').exists():
            print(f"\n🔧 Adding PID module access as well...")
            user.modules.add(pid_module)
            user.save()
            print("   ✅ PID access granted!")
    except Module.DoesNotExist:
        pass

    # Final check
    print(f"\n✅ Final modules for {user.email}:")
    for module in user.modules.all():
        print(f"   - {module.code}: {module.name}")

if __name__ == '__main__':
    check_and_assign_access()
