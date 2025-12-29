#!/usr/bin/env python
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Role, Module

def main():
    print("=" * 60)
    print("Available Roles")
    print("=" * 60)
    
    roles = Role.objects.all()
    for role in roles:
        print(f"\n{role.code}: {role.name}")
        modules = role.modules.all()
        print(f"  Modules: {', '.join([m.code for m in modules])}")
    
    print("\n" + "=" * 60)
    print("Available Modules")
    print("=" * 60)
    
    modules = Module.objects.all()
    for module in modules:
        print(f"  {module.code}: {module.name}")

if __name__ == '__main__':
    main()
