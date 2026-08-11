#!/usr/bin/env python
"""
Check and fix human_resource role in production.
No Unicode characters to avoid Windows PowerShell encoding issues.
"""
import django
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Suppress all Django startup output
import io
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()

try:
    django.setup()
finally:
    # Restore stdout/stderr
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__

from apps.rbac.models import Role, Module, RoleModule
from apps.rbac.rbac_config import ROLE_MODULE_POLICY

def main():
    try:
        role = Role.objects.get(code='human_resource')
        
        # Get current modules
        current_modules = set(role.modules.values_list('code', flat=True))
        
        # Get expected modules from policy
        expected_modules = set(ROLE_MODULE_POLICY.get('human_resource', []))
        
        # Calculate differences
        missing = expected_modules - current_modules
        extra = current_modules - expected_modules
        
        print(f"\n[BEFORE] human_resource role has {len(current_modules)} modules")
        print(f"Expected: {len(expected_modules)} modules")
        print(f"Missing: {len(missing)} | Extra: {len(extra)}")
        
        if missing or extra:
            print("\n[ACTION] Syncing modules...")
            
            # Remove extra modules
            for module_code in extra:
                try:
                    module = Module.objects.get(code=module_code)
                    RoleModule.objects.filter(role=role, module=module).delete()
                    print(f"  - Removed: {module_code}")
                except Module.DoesNotExist:
                    pass
            
            # Add missing modules
            for module_code in missing:
                try:
                    module = Module.objects.get(code=module_code)
                    RoleModule.objects.get_or_create(role=role, module=module)
                    print(f"  + Added: {module_code}")
                except Module.DoesNotExist:
                    print(f"  ! Module not found: {module_code}")
            
            # Verify fix
            updated_modules = set(role.modules.values_list('code', flat=True))
            print(f"\n[AFTER] human_resource role has {len(updated_modules)} modules")
            
            if updated_modules == expected_modules:
                print("[SUCCESS] Role modules synced perfectly!")
            else:
                print("[WARNING] Sync incomplete, check module definitions")
        else:
            print("[OK] Role is already in sync!")
            
    except Role.DoesNotExist:
        print("[ERROR] human_resource role not found in database!")
        return 1
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
