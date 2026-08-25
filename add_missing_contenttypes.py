"""
Add missing contenttypes.0001_initial migration record to fix dependency issue.
This is safe because the contenttypes tables already exist.
"""
import os
import sys
import django
from datetime import datetime, timezone

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection


def add_missing_contenttypes_migration():
    """
    Add the missing contenttypes.0001_initial migration record.
    This is safe because the tables already exist - we're just fixing the history.
    """
    print("\n" + "="*70)
    print("ADDING MISSING CONTENTTYPES MIGRATION RECORD")
    print("="*70 + "\n")
    
    with connection.cursor() as cursor:
        # Check if contenttypes tables exist
        print("📋 Checking if contenttypes tables exist...")
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'django_content_type'
            );
        """)
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print("❌ django_content_type table doesn't exist!")
            print("   Cannot proceed - database structure is broken.")
            return False
        
        print("✅ django_content_type table exists")
        
        # Check if contenttypes migration is already recorded
        cursor.execute("""
            SELECT id, applied 
            FROM django_migrations 
            WHERE app = 'contenttypes' AND name = '0001_initial';
        """)
        existing = cursor.fetchone()
        
        if existing:
            print(f"✅ contenttypes.0001_initial already recorded (ID: {existing[0]})")
            return True
        
        # Get the ID that should come before auth.0001_initial
        cursor.execute("""
            SELECT id, applied 
            FROM django_migrations 
            WHERE app = 'auth' AND name = '0001_initial';
        """)
        auth_record = cursor.fetchone()
        
        if not auth_record:
            print("❌ auth.0001_initial not found!")
            return False
        
        auth_id = auth_record[0]
        auth_applied = auth_record[1]
        
        print(f"\n📍 Found auth.0001_initial at ID: {auth_id}")
        print(f"   Applied: {auth_applied}")
        
        # We need to insert contenttypes before auth
        # Strategy: Insert with ID = auth_id - 1 (or use a lower ID)
        target_id = max(1, auth_id - 1)
        
        print(f"\n🔧 Adding contenttypes.0001_initial with ID: {target_id}")
        
        try:
            # First, shift any existing migrations at that ID
            cursor.execute("""
                UPDATE django_migrations 
                SET id = id + 10000 
                WHERE id >= %s AND id < %s;
            """, [target_id, auth_id])
            
            # Now insert the contenttypes migration
            cursor.execute("""
                INSERT INTO django_migrations (id, app, name, applied)
                VALUES (%s, %s, %s, %s);
            """, [target_id, 'contenttypes', '0001_initial', auth_applied])
            
            print("✅ contenttypes.0001_initial migration record added!")
            
            # Verify
            cursor.execute("""
                SELECT id, app, name 
                FROM django_migrations 
                WHERE app IN ('contenttypes', 'auth') AND name = '0001_initial'
                ORDER BY id;
            """)
            migrations = cursor.fetchall()
            
            print("\n✅ Migration order after fix:")
            for m in migrations:
                print(f"   ID: {m[0]}, App: {m[1]}, Name: {m[2]}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Error during fix: {e}")
            connection.rollback()
            return False
    
    print("\n" + "="*70)


if __name__ == '__main__':
    try:
        success = add_missing_contenttypes_migration()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
