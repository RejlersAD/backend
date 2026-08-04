#!/usr/bin/env python
"""
SMART MIGRATION CONFLICT FIXER FOR RAILWAY
==========================================
Handles migration inconsistencies in production database.
Specifically fixes the case where migrations are applied out of order.

This script:
1. Detects if 0006 is applied but 0005 is not
2. Fake-applies 0005 since it has no operations
3. Maintains database consistency

Usage: Called automatically by railway_start.sh before migrations
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.core.management import call_command

def check_migration_applied(app_label, migration_name):
    """Check if a migration is already applied"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app = %s AND name = %s",
            [app_label, migration_name]
        )
        return cursor.fetchone()[0] > 0

def fake_apply_migration(app_label, migration_name):
    """Fake-apply a migration (mark as applied without running operations)"""
    print(f"📝 Fake-applying {app_label}.{migration_name}")
    try:
        call_command('migrate', app_label, migration_name, fake=True, verbosity=1)
        print(f"✅ Successfully fake-applied {app_label}.{migration_name}")
        return True
    except Exception as e:
        print(f"❌ Failed to fake-apply: {e}")
        return False

def fix_migration_order():
    """Fix migration order inconsistencies"""
    print("\n" + "="*70)
    print("SMART MIGRATION CONSISTENCY CHECK".center(70))
    print("="*70 + "\n")
    
    # Check for the specific issue: 0006 applied but 0005 not applied
    migration_0005_applied = check_migration_applied('process_datasheet', '0005_merge_migrations')
    migration_0006_applied = check_migration_applied('process_datasheet', '0006_control_valve_delta_p')
    
    if migration_0006_applied and not migration_0005_applied:
        print("⚠️  INCONSISTENCY DETECTED:")
        print("   - 0006_control_valve_delta_p is applied")
        print("   - 0005_merge_migrations is NOT applied")
        print("\n🔧 FIXING: Fake-applying 0005 (no operations, safe)")
        
        if fake_apply_migration('process_datasheet', '0005_merge_migrations'):
            print("\n✅ Migration consistency restored!")
            print("   Database state is now consistent with migration graph")
            return True
        else:
            print("\n❌ Failed to restore consistency")
            return False
    elif migration_0005_applied:
        print("✅ Migration 0005 is already applied - no action needed")
        return True
    else:
        print("ℹ️  No migration inconsistency detected")
        print("   Both 0005 and 0006 are not applied, or migration chain is correct")
        return True

if __name__ == '__main__':
    try:
        success = fix_migration_order()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
