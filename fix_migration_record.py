#!/usr/bin/env python
"""
DIRECT DATABASE MIGRATION RECORD INSERTION
==========================================
Directly inserts migration 0005 record into django_migrations table
to fix the case where 0006 was applied before 0005.

This bypasses Django's migration system and directly manipulates the database.
"""

import os
import sys
import django
from datetime import datetime

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

def insert_migration_record():
    """Directly insert 0005_merge_migrations into django_migrations table"""
    print("\n" + "="*70)
    print("DIRECT MIGRATION RECORD INSERTION".center(70))
    print("="*70 + "\n")
    
    # Check if 0005 already exists
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app = %s AND name = %s",
            ['process_datasheet', '0005_merge_migrations']
        )
        exists = cursor.fetchone()[0] > 0
        
        if exists:
            print("✅ Migration 0005_merge_migrations already exists in database")
            return True
        
        # Check if 0006 exists
        cursor.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app = %s AND name = %s",
            ['process_datasheet', '0006_control_valve_delta_p']
        )
        has_0006 = cursor.fetchone()[0] > 0
        
        if not has_0006:
            print("ℹ️  Migration 0006 not applied yet - no action needed")
            return True
        
        print("⚠️  INCONSISTENCY DETECTED:")
        print("   - 0006_control_valve_delta_p is applied")
        print("   - 0005_merge_migrations is NOT applied")
        print("\n🔧 FIXING: Directly inserting 0005 migration record...")
        
        # Directly insert the migration record
        try:
            cursor.execute(
                """
                INSERT INTO django_migrations (app, name, applied)
                VALUES (%s, %s, %s)
                """,
                ['process_datasheet', '0005_merge_migrations', datetime.now()]
            )
            print("✅ Successfully inserted 0005_merge_migrations record")
            print("   Database migration history is now consistent")
            return True
        except Exception as e:
            print(f"❌ Failed to insert record: {e}")
            return False

if __name__ == '__main__':
    try:
        success = insert_migration_record()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
