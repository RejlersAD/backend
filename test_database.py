#!/usr/bin/env python3
"""
Database Verification Script
Tests PostgreSQL database connectivity and data integrity
"""

import os
import sys

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection
from django.core.exceptions import ImproperlyConfigured

User = get_user_model()

def test_database_connection():
    """Test basic database connectivity"""
    print("\n=== Database Connection Test ===")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            print(f"✓ Connected to PostgreSQL")
            print(f"  Version: {version}")
        return True
    except Exception as e:
        print(f"✗ Database connection failed: {str(e)}")
        return False

def test_user_table():
    """Test user table access"""
    print("\n=== User Table Test ===")
    try:
        total_users = User.objects.count()
        print(f"✓ User table accessible")
        print(f"  Total users: {total_users}")
        
        if total_users > 0:
            superusers = User.objects.filter(is_superuser=True)
            print(f"  Superusers: {superusers.count()}")
            for user in superusers:
                print(f"    - {user.email} (Active: {user.is_active})")
        
        return True
    except Exception as e:
        print(f"✗ User table access failed: {str(e)}")
        return False

def test_database_tables():
    """List all tables in database"""
    print("\n=== Database Tables ===")
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
            tables = cursor.fetchall()
            print(f"✓ Found {len(tables)} tables:")
            for table in tables[:10]:  # Show first 10
                print(f"    - {table[0]}")
            if len(tables) > 10:
                print(f"    ... and {len(tables) - 10} more")
        return True
    except Exception as e:
        print(f"✗ Failed to list tables: {str(e)}")
        return False

def test_migrations():
    """Check migration status"""
    print("\n=== Migration Status ===")
    try:
        from django.db.migrations.recorder import MigrationRecorder
        recorder = MigrationRecorder(connection)
        applied = recorder.applied_migrations()
        print(f"✓ Applied migrations: {len(applied)}")
        return True
    except Exception as e:
        print(f"✗ Migration check failed: {str(e)}")
        return False

def main():
    print("="*60)
    print("AIFlow Database Verification".center(60))
    print("="*60)
    
    results = []
    results.append(("Connection", test_database_connection()))
    results.append(("Tables", test_database_tables()))
    results.append(("Migrations", test_migrations()))
    results.append(("User Table", test_user_table()))
    
    print("\n" + "="*60)
    print("Summary".center(60))
    print("="*60)
    
    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{test_name:<20} : {status}")
    
    passed_count = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed_count}/{len(results)} tests passed")
    
    return 0 if passed_count == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())
