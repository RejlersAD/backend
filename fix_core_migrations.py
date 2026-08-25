"""
Comprehensive migration history repair - adds all missing Django core migrations.
"""
import os
import sys
import django
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection


def add_missing_core_migrations():
    """
    Add all missing core Django migrations (contenttypes, auth, admin, sessions).
    """
    print("\n" + "="*70)
    print("COMPREHENSIVE CORE MIGRATIONS FIX")
    print("="*70 + "\n")
    
    base_time = datetime(2025, 12, 12, 14, 4, 53, tzinfo=timezone.utc)
    
    # Define all core migrations in correct order
    core_migrations = [
        (1, 'contenttypes', '0001_initial'),
        (2, 'contenttypes', '0002_remove_content_type_name'),
        (3, 'auth', '0001_initial'),
        (4, 'auth', '0002_alter_permission_name_max_length'),
        (5, 'auth', '0003_alter_user_email_max_length'),
        (6, 'auth', '0004_alter_user_username_opts'),
        (7, 'auth', '0005_alter_user_last_login_null'),
        (8, 'auth', '0006_require_contenttypes_0002'),
        (9, 'auth', '0007_alter_validators_add_error_messages'),
        (10, 'auth', '0008_alter_user_username_max_length'),
        (11, 'auth', '0009_alter_user_last_name_max_length'),
        (12, 'auth', '0010_alter_group_name_max_length'),
        (13, 'auth', '0011_update_proxy_permissions'),
        (14, 'auth', '0012_alter_user_first_name_max_length'),
        (15, 'admin', '0001_initial'),
        (16, 'admin', '0002_logentry_remove_auto_add'),
        (17, 'admin', '0003_logentry_add_action_flag_choices'),
        (18, 'sessions', '0001_initial'),
    ]
    
    with connection.cursor() as cursor:
        # Get existing migrations
        cursor.execute("""
            SELECT app, name, id, applied 
            FROM django_migrations 
            WHERE app IN ('contenttypes', 'auth', 'admin', 'sessions')
            ORDER BY id;
        """)
        existing = {(app, name): (mid, applied) for app, name, mid, applied in cursor.fetchall()}
        
        print(f"📋 Found {len(existing)} existing core migrations")
        
        # Identify missing migrations
        missing = []
        for target_id, app, name in core_migrations:
            if (app, name) not in existing:
                missing.append((target_id, app, name))
        
        if not missing:
            print("✅ All core migrations present!")
            
            # Check if order is correct
            print("\n📋 Verifying order...")
            for target_id, app, name in core_migrations:
                if (app, name) in existing:
                    actual_id, _ = existing[(app, name)]
                    if actual_id != target_id:
                        print(f"⚠️  {app}.{name}: expected ID {target_id}, actual ID {actual_id}")
            
            return True
        
        print(f"\n❌ Missing {len(missing)} core migrations:")
        for target_id, app, name in missing:
            print(f"   ID {target_id:2d}: {app}.{name}")
        
        print("\n🔧 Rebuilding core migrations in correct order...\n")
        
        try:
            # Strategy: Move all migrations to temp IDs, rebuild core, then move others back
            print("📝 Step 1: Moving all migrations to temporary IDs...")
            cursor.execute("""
                UPDATE django_migrations 
                SET id = id + 2000000 
                WHERE id < 2000000;
            """)
            
            print("📝 Step 2: Inserting complete core migration set...")
            for target_id, app, name in core_migrations:
                cursor.execute("""
                    INSERT INTO django_migrations (id, app, name, applied)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING;
                """, [target_id, app, name, base_time])
                print(f"   ✅ ID {target_id:2d}: {app}.{name}")
            
            print("\n📝 Step 3: Moving non-core migrations back...")
            # Move non-core migrations back, starting from ID 19
            cursor.execute("""
                SELECT id, app, name, applied 
                FROM django_migrations 
                WHERE id >= 2000000 
                  AND app NOT IN ('contenttypes', 'auth', 'admin', 'sessions')
                ORDER BY id;
            """)
            other_migrations = cursor.fetchall()
            
            next_id = 19
            for old_id, app, name, applied in other_migrations:
                cursor.execute("""
                    UPDATE django_migrations 
                    SET id = %s 
                    WHERE id = %s;
                """, [next_id, old_id])
                next_id += 1
            
            # Clean up any duplicate core migrations in temp range
            cursor.execute("""
                DELETE FROM django_migrations 
                WHERE id >= 2000000 
                  AND app IN ('contenttypes', 'auth', 'admin', 'sessions');
            """)
            
            print(f"   ✅ Moved {len(other_migrations)} migrations")
            
            print("\n✅ Core migrations rebuilt successfully!\n")
            
            # Verify
            cursor.execute("""
                SELECT id, app, name 
                FROM django_migrations 
                WHERE app IN ('contenttypes', 'auth', 'admin', 'sessions')
                ORDER BY id;
            """)
            final = cursor.fetchall()
            
            print("✅ Final core migrations:")
            for mid, app, name in final:
                print(f"   ID {mid:2d}: {app}.{name}")
            
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
        success = add_missing_core_migrations()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
