import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module

modules = Module.objects.filter(is_active=True).values('code', 'name')

print('\n📋 All Active Modules:')
for m in modules:
    print(f'  Code: {m["code"]:25s} | Name: {m["name"]}')
