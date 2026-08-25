"""
Check auth migrations in database to see what's wrong.
"""
import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection


with connection.cursor() as cursor:
    cursor.execute("""
        SELECT id, app, name, applied 
        FROM django_migrations 
        WHERE app = 'auth'
        ORDER BY id;
    """)
    auth_migrations = cursor.fetchall()
    
    print("\n" + "="*70)
    print("AUTH MIGRATIONS IN DATABASE")
    print("="*70 + "\n")
    
    for mid, app, name, applied in auth_migrations:
        print(f"ID {mid:3d}: {app}.{name} (applied: {applied})")
    
    print("\n" + "="*70)
