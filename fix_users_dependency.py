"""
Smart fix for users.0001_initial dependency issue.
Adds missing auth.0012_alter_user_first_name_max_length migration.
"""
import os
import sys
import django
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection


def fix_users_dependency():
    """
    Fix the users.0001_initial dependency issue by adding missing auth.0012.
    """
    print("\n" + "="*70)
    print("SMART FIX: Adding Missing auth.0012 Migration")
    print("="*70 + "\n")
    
    with connection.cursor() as cursor:
        # Check current state
        cursor.execute("""
            SELECT id, app, name, applied 
            FROM django_migrations 
            WHERE (app = 'auth' AND name = '0012_alter_user_first_name_max_length')
               OR (app = 'users' AND name = '0001_initial')
            ORDER BY id;
        """)
        migrations = cursor.fetchall()
        
        print("📋 Current state:")
        for mid, app, name, applied in migrations:
            print(f"   ID {mid:3d}: {app}.{name}")
        
        # Check if auth.0012 exists
        auth_12_exists = any(app == 'auth' and name == '0012_alter_user_first_name_max_length' 
                            for _, app, name, _ in migrations)
        
        if auth_12_exists:
            print("\n✅ auth.0012_alter_user_first_name_max_length already exists!")
            
            # Check order - it should come before users.0001_initial
            users_001_id = next((mid for mid, app, name, _ in migrations 
                               if app == 'users' and name == '0001_initial'), None)
            auth_12_id = next((mid for mid, app, name, _ in migrations 
                              if app == 'auth' and name == '0012_alter_user_first_name_max_length'), None)
            
            if users_001_id and auth_12_id and auth_12_id > users_001_id:
                print(f"\n❌ Order issue: auth.0012 (ID {auth_12_id}) comes AFTER users.0001 (ID {users_001_id})")
                print("🔧 Swapping order...")
                
                # Swap IDs
                temp_id = 999999
                cursor.execute("UPDATE django_migrations SET id = %s WHERE id = %s", [temp_id, auth_12_id])
                cursor.execute("UPDATE django_migrations SET id = %s WHERE id = %s", [auth_12_id, users_001_id])
                cursor.execute("UPDATE django_migrations SET id = %s WHERE id = %s", [users_001_id, temp_id])
                
                print(f"✅ Swapped: auth.0012 now at ID {users_001_id}, users.0001 now at ID {auth_12_id}")
            else:
                print("\n✅ Migration order is correct!")
            
            return True
        
        # auth.0012 doesn't exist - add it
        print("\n❌ auth.0012_alter_user_first_name_max_length is missing!")
        
        # Get auth.0011 ID to insert after it
        cursor.execute("""
            SELECT id, applied 
            FROM django_migrations 
            WHERE app = 'auth' AND name = '0011_update_proxy_permissions';
        """)
        auth_11 = cursor.fetchone()
        
        if not auth_11:
            print("❌ auth.0011_update_proxy_permissions not found! Cannot proceed.")
            return False
        
        auth_11_id, auth_11_applied = auth_11
        print(f"\n📍 Found auth.0011 at ID {auth_11_id}")
        
        # Get users.0001_initial ID
        users_001 = next((mid for mid, app, name, _ in migrations 
                         if app == 'users' and name == '0001_initial'), None)
        
        if not users_001:
            print("⚠️  users.0001_initial not found - adding auth.0012 after auth.0011")
            target_id = auth_11_id + 1
        else:
            print(f"📍 Found users.0001 at ID {users_001}")
            # Insert auth.0012 between auth.0011 and users.0001
            target_id = auth_11_id + 1
            
            if target_id >= users_001:
                # Need to shift users.0001 and everything after it
                print(f"\n🔧 Shifting migrations >= ID {users_001} to make room...")
                cursor.execute("""
                    UPDATE django_migrations 
                    SET id = id + 1 
                    WHERE id >= %s;
                """, [users_001])
        
        # Insert auth.0012
        print(f"\n🔧 Inserting auth.0012 at ID {target_id}...")
        cursor.execute("""
            INSERT INTO django_migrations (id, app, name, applied)
            VALUES (%s, %s, %s, %s);
        """, [target_id, 'auth', '0012_alter_user_first_name_max_length', auth_11_applied])
        
        print("✅ auth.0012_alter_user_first_name_max_length added!")
        
        # Verify
        cursor.execute("""
            SELECT id, app, name 
            FROM django_migrations 
            WHERE (app = 'auth' AND name LIKE '0012%')
               OR (app = 'auth' AND name = '0011_update_proxy_permissions')
               OR (app = 'users' AND name = '0001_initial')
            ORDER BY id;
        """)
        final = cursor.fetchall()
        
        print("\n✅ Final migration order:")
        for mid, app, name in final:
            print(f"   ID {mid:3d}: {app}.{name}")
        
        return True
    
    print("\n" + "="*70)


if __name__ == '__main__':
    try:
        success = fix_users_dependency()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
