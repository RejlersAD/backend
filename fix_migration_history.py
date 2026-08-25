"""
Safe script to fix inconsistent migration history in production database.
This specifically addresses the auth.0001_initial / contenttypes.0001_initial dependency issue.
"""
import os
import sys
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


def fix_migration_history():
    """
    Fix inconsistent migration history by reordering contenttypes.0001_initial
    to come before auth.0001_initial in the django_migrations table.
    """
    print("\n" + "="*70)
    print("FIXING MIGRATION HISTORY - PRODUCTION SAFE")
    print("="*70 + "\n")
    
    with connection.cursor() as cursor:
        # Check current state
        print("📋 Checking current migration order...")
        cursor.execute("""
            SELECT id, app, name, applied 
            FROM django_migrations 
            WHERE (app = 'contenttypes' AND name = '0001_initial')
               OR (app = 'auth' AND name = '0001_initial')
            ORDER BY id;
        """)
        migrations = cursor.fetchall()
        
        if len(migrations) < 2:
            print("❌ Could not find both migrations. Current state:")
            for m in migrations:
                print(f"   ID: {m[0]}, App: {m[1]}, Name: {m[2]}, Applied: {m[3]}")
            print("\n⚠️  Cannot proceed - both migrations must exist.")
            return False
        
        contenttypes_record = None
        auth_record = None
        
        for m in migrations:
            print(f"   ID: {m[0]}, App: {m[1]}, Name: {m[2]}, Applied: {m[3]}")
            if m[1] == 'contenttypes':
                contenttypes_record = m
            elif m[1] == 'auth':
                auth_record = m
        
        if not contenttypes_record or not auth_record:
            print("\n❌ Missing required migrations")
            return False
        
        # Check if fix is needed
        if contenttypes_record[0] < auth_record[0]:
            print("\n✅ Migration history is already correct!")
            print(f"   contenttypes.0001_initial (ID: {contenttypes_record[0]}) comes before")
            print(f"   auth.0001_initial (ID: {auth_record[0]})")
            return True
        
        print(f"\n❌ INCONSISTENCY DETECTED:")
        print(f"   auth.0001_initial (ID: {auth_record[0]}) comes before")
        print(f"   contenttypes.0001_initial (ID: {contenttypes_record[0]})")
        print(f"\n🔧 Fixing by swapping IDs...")
        
        # Swap the IDs to fix the order
        # This is safe because we're only changing the order, not the content
        temp_id = 999999
        
        try:
            # Move auth to temp ID
            cursor.execute(
                "UPDATE django_migrations SET id = %s WHERE id = %s",
                [temp_id, auth_record[0]]
            )
            
            # Move contenttypes to auth's old ID
            cursor.execute(
                "UPDATE django_migrations SET id = %s WHERE id = %s",
                [auth_record[0], contenttypes_record[0]]
            )
            
            # Move auth from temp to contenttypes' old ID
            cursor.execute(
                "UPDATE django_migrations SET id = %s WHERE id = %s",
                [contenttypes_record[0], temp_id]
            )
            
            print("✅ Migration history fixed successfully!")
            
            # Verify the fix
            cursor.execute("""
                SELECT id, app, name 
                FROM django_migrations 
                WHERE (app = 'contenttypes' AND name = '0001_initial')
                   OR (app = 'auth' AND name = '0001_initial')
                ORDER BY id;
            """)
            migrations_after = cursor.fetchall()
            
            print("\n✅ New migration order:")
            for m in migrations_after:
                print(f"   ID: {m[0]}, App: {m[1]}, Name: {m[2]}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Error during fix: {e}")
            connection.rollback()
            return False
    
    print("\n" + "="*70)


if __name__ == '__main__':
    try:
        success = fix_migration_history()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
