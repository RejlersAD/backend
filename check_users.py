"""Check users"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

print("\nAll users in database:")
for user in User.objects.all().order_by('id'):
    print(f"  ID {user.id}: {user.email} ({user.username})")
