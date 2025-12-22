"""
Test modules API and check data
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module
from apps.rbac.serializers import ModuleSerializer

modules = Module.objects.filter(is_active=True)

print(f"Active modules in DB: {modules.count()}\n")

for module in modules:
    print(f"Module: {module.name}")
    print(f"  ID: {module.id}")
    print(f"  Code: {module.code}")
    print(f"  Active: {module.is_active}")
    print(f"  Icon: {module.icon}")
    print()

print("\nSerialized module data (as API returns):")
serializer = ModuleSerializer(modules, many=True)
import json
print(json.dumps(serializer.data[:3], indent=2))
