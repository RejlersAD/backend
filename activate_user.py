"""Activate the xerxez.in@gmail.com user"""
import os
import sys
import django

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User

email = "xerxez.in@gmail.com"

try:
    user = User.objects.get(email=email)
    print(f"Found user: {user.email}")
    print(f"Current is_active status: {user.is_active}")
    
    user.is_active = True
    user.save()
    
    print(f"✅ User {email} has been activated!")
    print(f"New is_active status: {user.is_active}")
    print("\nThe user should now appear in the dashboard.")
    
except User.DoesNotExist:
    print(f"❌ User {email} not found in database")
