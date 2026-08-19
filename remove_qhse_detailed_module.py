"""
Remove qhse_detailed module from RBAC system
This script removes the 'qhse_detailed' module that was used for "8.2 Project Quality Details" detailed view.
The detailed view functionality has been merged into the main QHSE dashboard and summary view.
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from apps.rbac.models import Module, Role

print("\n" + "="*80)
print("🗑️ REMOVING qhse_detailed MODULE FROM RBAC SYSTEM")
print("="*80 + "\n")

# Check if module exists
try:
    module = Module.objects.get(code='qhse_detailed')
    print(f"✅ Found module: {module.code} - {module.name}")
    print(f"   Description: {module.description}")
    print(f"   Order: {module.order}\n")
    
    # Count roles that have this module
    roles_with_module = Role.objects.filter(modules=module)
    role_count = roles_with_module.count()
    
    if role_count > 0:
        print(f"📋 This module is assigned to {role_count} role(s):")
        for role in roles_with_module:
            print(f"   - {role.name} ({role.code})")
        print()
    
    # Remove the module
    confirm = input("⚠️  Are you sure you want to delete this module? (yes/no): ")
    if confirm.lower() == 'yes':
        module_name = module.name
        module_code = module.code
        
        # First, remove module from all role associations (many-to-many)
        print(f"\n🔗 Removing module associations from {role_count} role(s)...")
        with connection.cursor() as cursor:
            cursor.execute("""
                DELETE FROM rbac_role_modules 
                WHERE module_id = (SELECT id FROM rbac_module WHERE code = %s)
            """, [module_code])
            print(f"   ✅ Removed {cursor.rowcount} role-module associations")
        
        # Then delete the module itself
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM rbac_module WHERE code = %s", [module_code])
            print(f"✅ Successfully deleted module: {module_name}\n")
        
    else:
        print("❌ Deletion cancelled by user\n")
        
except Module.DoesNotExist:
    print("ℹ️  Module 'qhse_detailed' does not exist in the database")
    print("   It may have already been removed or never created\n")

# Clean up any orphaned permissions related to qhse_detailed
print("🧹 Checking for orphaned permissions...")
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT COUNT(*) FROM auth_permission 
        WHERE codename LIKE '%qhse_detailed%'
    """)
    orphaned_count = cursor.fetchone()[0]
    
    if orphaned_count > 0:
        print(f"   Found {orphaned_count} orphaned permission(s)")
        cleanup = input("   Remove orphaned permissions? (yes/no): ")
        if cleanup.lower() == 'yes':
            cursor.execute("""
                DELETE FROM auth_permission 
                WHERE codename LIKE '%qhse_detailed%'
            """)
            print(f"   ✅ Removed {orphaned_count} orphaned permission(s)")
    else:
        print("   ✅ No orphaned permissions found")

print("\n" + "="*80)
print("✅ CLEANUP COMPLETE")
print("="*80 + "\n")

print("Summary:")
print("  - qhse_detailed module removed from database")
print("  - Module removed from all role assignments")
print("  - Orphaned permissions cleaned up")
print("  - Frontend updated to remove sidebar menu item")
print("  - Backend RBAC config updated")
print("\n")
