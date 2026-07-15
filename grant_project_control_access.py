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


if __name__ == '__main__':
    try:
        success = grant_project_control_to_roles()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
