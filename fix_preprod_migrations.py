#!/usr/bin/env python
"""
Preprod Migration Conflict Resolver
===================================
This script fixes database migration conflicts in preprod environment only.
It removes conflicting migration records from django_migrations table.

SOFT-CODED: Only runs in Railway/preprod environment (checks RAILWAY_STATIC_URL)
"""
import os
import sys
import django

# Check if running in Railway/preprod environment
IS_PREPROD = bool(os.environ.get('RAILWAY_STATIC_URL'))

if not IS_PREPROD:
    print("ℹ️  Not in preprod environment - skipping migration conflict resolution")
    sys.exit(0)

print("=" * 60)
print("🔧 PREPROD MIGRATION CONFLICT RESOLVER")
print("=" * 60)

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

# Define conflicting migrations to remove
CONFLICTING_MIGRATIONS = [
    {
        'app': 'process_datasheet',
        'name': '0018_merge_20260306_1348',
        'reason': 'Duplicate merge migration - keeping 0018_merge_20260306_1236'
    }
]

def check_migration_exists(app_label, migration_name):
    """Check if a migration record exists in the database"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app = %s AND name = %s",
            [app_label, migration_name]
        )
        return cursor.fetchone()[0] > 0

def remove_migration_record(app_label, migration_name):
    """Remove a migration record from the database"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM django_migrations WHERE app = %s AND name = %s",
                [app_label, migration_name]
            )
            return True
    except Exception as e:
        print(f"❌ Error removing migration: {e}")
        return False

def main():
    """Main function to resolve migration conflicts"""
    
    # Check if django_migrations table exists
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'django_migrations'
            )
        """)
        if not cursor.fetchone()[0]:
            print("ℹ️  django_migrations table doesn't exist yet - fresh database")
            print("✅ No conflicts to resolve")
            return 0
    
    conflicts_found = False
    conflicts_resolved = 0
    
    print("\n📋 Checking for migration conflicts...")
    print("-" * 60)
    
    for conflict in CONFLICTING_MIGRATIONS:
        app_label = conflict['app']
        migration_name = conflict['name']
        reason = conflict['reason']
        
        if check_migration_exists(app_label, migration_name):
            conflicts_found = True
            print(f"\n⚠️  Found conflicting migration:")
            print(f"   App: {app_label}")
            print(f"   Migration: {migration_name}")
            print(f"   Reason: {reason}")
            print(f"   Action: Removing from database...")
            
            if remove_migration_record(app_label, migration_name):
                conflicts_resolved += 1
                print(f"   ✅ Successfully removed")
            else:
                print(f"   ❌ Failed to remove")
                return 1
        else:
            print(f"✓ {app_label}.{migration_name} - not in database (OK)")
    
    print("\n" + "=" * 60)
    
    if conflicts_found:
        print(f"✅ RESOLVED: {conflicts_resolved} conflicting migration(s) removed")
        print("   Normal migration process can now proceed")
    else:
        print("✅ NO CONFLICTS: Database migration state is clean")
    
    print("=" * 60)
    return 0

if __name__ == '__main__':
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
