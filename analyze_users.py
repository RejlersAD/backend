#!/usr/bin/env python
"""
Database User Analysis Script
Check current user count and analyze user data
"""
import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from django.db import connection

def analyze_users():
    """Analyze user data in the database"""
    
    print("🔍 DATABASE USER ANALYSIS")
    print("=" * 50)
    
    # Basic user statistics
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    inactive_users = User.objects.filter(is_active=False).count()
    admin_users = User.objects.filter(is_staff=True).count()
    superusers = User.objects.filter(is_superuser=True).count()
    
    print(f"📊 USER STATISTICS:")
    print(f"   Total Users: {total_users}")
    print(f"   Active Users: {active_users}")  
    print(f"   Inactive Users: {inactive_users}")
    print(f"   Admin Users: {admin_users}")
    print(f"   Superusers: {superusers}")
    
    print(f"\n👥 ALL USERS IN DATABASE:")
    users = User.objects.all().order_by('date_joined')
    for i, user in enumerate(users, 1):
        status = "✅ Active" if user.is_active else "❌ Inactive"
        role = "🔑 Admin" if user.is_staff else "👤 User"
        print(f"   {i}. {user.username} ({user.email})")
        print(f"      Status: {status} | Role: {role}")
        print(f"      Created: {user.date_joined.strftime('%Y-%m-%d %H:%M')}")
        print()
    
    # Check database table directly
    print("🗄️ DIRECT DATABASE TABLE ANALYSIS:")
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM users_user")
        db_count = cursor.fetchone()[0]
        print(f"   Direct table count: {db_count}")
        
        cursor.execute("SELECT username, email, is_active, is_staff, date_joined FROM users_user ORDER BY date_joined LIMIT 10")
        rows = cursor.fetchall()
        print(f"   First 10 users from table:")
        for row in rows:
            username, email, is_active, is_staff, date_joined = row
            print(f"     - {username} ({email}) | Active: {is_active} | Staff: {is_staff} | Created: {date_joined}")
    
    return total_users

if __name__ == '__main__':
    try:
        user_count = analyze_users()
        print(f"\n✅ Analysis complete. Found {user_count} users in database.")
    except Exception as e:
        print(f"❌ Error analyzing users: {e}")
        sys.exit(1)