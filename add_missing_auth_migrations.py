"""
Add missing auth migration records to fix dependency chain.
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


def add_missing_auth_migrations():
    """
    Add missing auth migrations that are causing dependency errors.
    These migrations were applied but not recorded in django_migrations table.
    """
    print("\n" + "="*70)
    print("ADDING MISSING AUTH MIGRATION RECORDS")
    print("="*70 + "\n")
    
    # Define missing migrations and where they should be inserted
    missing_migrations = [
        ('auth', '0002_alter_permission_name_max_length', 4),  # between 0001 (ID 3) and 0004 (ID 6)
        ('auth', '0003_alter_user_email_max_length', 5),      # between 0002 and 0004
        ('auth', '0006_require_contenttypes_0002', 8),        # between 0005 (ID 7) and 0008 (ID 10)
        ('auth', '0007_alter_validators_add_error_messages', 9),  # between 0006 and 0008
        ('auth', '0009_alter_user_last_name_max_length', 11),  # between 0008 (ID 10) and 0011 (ID 13)
        ('auth', '0010_alter_group_name_max_length', 12),     # between 0009 and 0011
    ]
    
    with connection.cursor() as cursor:
        # Get current auth migrations
        cursor.execute("""
            SELECT name, id, applied 
            FROM django_migrations 
            WHERE app = 'auth'
            ORDER BY id;
        """)
        existing = {name: (mid, applied) for name, mid, applied in cursor.fetchall()}
        
        print("📋 Current auth migrations:")
        for name, (mid, applied) in existing.items():
            print(f"   ID {mid:3d}: auth.{name}")
        
        print(f"\n🔧 Adding {len(missing_migrations)} missing auth migrations...\n")
        
        try:
            # Now insert missing migrations and rearrange
            base_time = datetime(2025, 12, 12, 14, 4, 53, tzinfo=timezone.utc)
            
            all_auth_migrations = [
                (3, 'auth', '0001_initial', base_time),
                (4, 'auth', '0002_alter_permission_name_max_length', base_time),
                (5, 'auth', '0003_alter_user_email_max_length', base_time),
                (6, 'auth', '0004_alter_user_username_opts', base_time),
                (7, 'auth', '0005_alter_user_last_login_null', base_time),
                (8, 'auth', '0006_require_contenttypes_0002', base_time),
                (9, 'auth', '0007_alter_validators_add_error_messages', base_time),
                (10, 'auth', '0008_alter_user_username_max_length', base_time),
                (11, 'auth', '0009_alter_user_last_name_max_length', base_time),
                (12, 'auth', '0010_alter_group_name_max_length', base_time),
                (13, 'auth', '0011_update_proxy_permissions', base_time),
            ]
            
            # Strategy: shift all migrations to high temp IDs, delete auth, then insert all
            print("📝 Step 1: Shifting ALL migrations to temporary IDs...")
            cursor.execute("""
                UPDATE django_migrations 
                SET id = id + 1000000 
                WHERE id < 1000000;
            """)
            
            print("📝 Step 2: Deleting old auth records...")
            cursor.execute("DELETE FROM django_migrations WHERE app = 'auth';")
            
            print("📝 Step 3: Inserting complete auth migration set...")
            # Insert all auth migrations in correct order
            for mid, app, name, applied in all_auth_migrations:
                cursor.execute("""
                    INSERT INTO django_migrations (id, app, name, applied)
                    VALUES (%s, %s, %s, %s);
                """, [mid, app, name, applied])
                print(f"   ✅ Added ID {mid:3d}: {app}.{name}")
            
            print("📝 Step 4: Shifting other migrations back (avoiding auth ID range)...")
            # Only shift back migrations that will end up with ID > 13 (after auth)
            cursor.execute("""
                UPDATE django_migrations 
                SET id = id - 1000000 
                WHERE id > 1000000 AND (id - 1000000) > 13;
            """)
            
            # For migrations that would conflict (IDs 1-13), shift to start at 14
            cursor.execute("""
                SELECT id, app, name, applied
                FROM django_migrations 
                WHERE id > 1000000 AND (id - 1000000) <= 13
                ORDER BY id;
            """)
            conflicting = cursor.fetchall()
            
            if conflicting:
                print(f"📝 Step 5: Relocating {len(conflicting)} migrations that would conflict...")
                next_id = 14
                for old_id, app, name, applied in conflicting:
                    cursor.execute("""
                        UPDATE django_migrations 
                        SET id = %s 
                        WHERE id = %s;
                    """, [next_id, old_id])
                    print(f"   ✅ Moved {app}.{name} from temp ID {old_id} to ID {next_id}")
                    next_id += 1
            
            print("\n✅ All auth migrations added successfully!\n")
            
            # Verify
            cursor.execute("""
                SELECT id, name 
                FROM django_migrations 
                WHERE app = 'auth'
                ORDER BY id;
            """)
            final = cursor.fetchall()
            
            print("✅ Final auth migration order:")
            for mid, name in final:
                print(f"   ID {mid:3d}: auth.{name}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            connection.rollback()
            return False
    
    print("\n" + "="*70)


if __name__ == '__main__':
    try:
        success = add_missing_auth_migrations()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
