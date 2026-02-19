import os
import sys
import django

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from apps.users.models import User

db = settings.DATABASES['default']
print('\n' + '='*60)
print('✅ SMART DATABASE CONNECTION ACTIVE')
print('='*60)
print(f'Host:     {db.get("HOST", "unknown")}')
print(f'Database: {db.get("NAME", "unknown")}')
print(f'Port:     {db.get("PORT", "unknown")}')
print(f'Users:    {User.objects.count()}')
print('='*60 + '\n')
