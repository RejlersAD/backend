"""
Check for duplicate email addresses in the database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db.models import Count

User = get_user_model()

print('🔍 CHECKING FOR DUPLICATE EMAILS')
print('=' * 60)

# Find duplicate emails
duplicates = User.objects.values('email').annotate(count=Count('id')).filter(count__gt=1)

if duplicates:
    print(f'Found {len(duplicates)} duplicate emails:\n')
    
    for dup in duplicates:
        users = User.objects.filter(email=dup['email']).order_by('id')
        print(f"Email: {dup['email']} - Count: {dup['count']}")
        
        for u in users:
            print(f"  ID: {u.id}")
            print(f"  Username: {u.username}")
            print(f"  First Name: {u.first_name}")
            print(f"  Last Name: {u.last_name}")
            print(f"  Created: {u.date_joined}")
            print(f"  Last Login: {u.last_login}")
            print(f"  Is Active: {u.is_active}")
            print(f"  Is Staff: {u.is_staff}")
            print(f"  Is Superuser: {u.is_superuser}")
            print("  ---")
        print()
else:
    print('✅ No duplicate emails found!')

print('=' * 60)
