"""
Smart Data Sync: Preprod → Production

Syncs users and profiles using raw SQL for maximum compatibility

Usage:
    python smart_sync_to_production.py
"""

import psycopg2
from psycopg2.extras import execute_values
import sys

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"


print("=" * 80)
print("SMART DATA SYNC: Preprod → Production")
print("=" * 80)
print("\n⚠️  This will copy ALL USERS (348) from preprod to production")
print()

response = input("Continue? (yes/no): ")
if response.lower() != 'yes':
    print("❌ Aborted by user")
    sys.exit(0)

try:
    # Connect to both databases
    print("\n🔌 Connecting to databases...")
    preprod_conn = psycopg2.connect(PREPROD_DB)
    prod_conn = psycopg2.connect(PROD_DB)
    
    preprod_cur = preprod_conn.cursor()
    prod_cur = prod_conn.cursor()
    
    print("   ✅ Connected to preprod")
    print("   ✅ Connected to production")
    
    # Step 1: Sync Users
    print("\n" + "=" * 80)
    print("STEP 1: SYNCING USERS")
    print("=" * 80)
    
    preprod_cur.execute("""
        SELECT id, password, last_login, is_superuser, username, first_name, 
               last_name, is_staff, is_active, date_joined, email, phone_number,
               avatar, bio, is_verified, is_first_login, last_password_change,
               must_reset_password, temp_password_created_at
        FROM users
        ORDER BY date_joined
    """)
    
    users = preprod_cur.fetchall()
    print(f"\n📊 Found {len(users)} users in preprod")
    
    created = 0
    skipped = 0
    errors = []
    
    for user_data in users:
        email = user_data[10]  # email is at index 10
        username = user_data[4]  # username is at index 4
        
        try:
            # Check if user already exists in production
            prod_cur.execute("SELECT COUNT(*) FROM users WHERE email = %s", (email,))
            exists = prod_cur.fetchone()[0] > 0
            
            if exists:
                print(f"   ⏭️  {email}")
                skipped += 1
                continue
            
            # Insert user into production
            prod_cur.execute("""
                INSERT INTO users (
                    id, password, last_login, is_superuser, username, first_name,
                    last_name, is_staff, is_active, date_joined, email, phone_number,
                    avatar, bio, is_verified, is_first_login, last_password_change,
                    must_reset_password, temp_password_created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, user_data)
            
            print(f"   ✅ {email} ({username})")
            created += 1
            
        except Exception as e:
            error_msg = f"{email}: {str(e)[:60]}"
            print(f"   ❌ {error_msg}")
            errors.append(error_msg)
    
    # Commit user inserts
    prod_conn.commit()
    
    print(f"\n📊 USER SYNC SUMMARY:")
    print(f"   ✅ Created:  {created}")
    print(f"   ⏭️  Skipped:  {skipped}")
    print(f"   ❌ Errors:   {len(errors)}")
    
    if errors:
        print("\n⚠️  ERRORS DETAIL:")
        for error in errors[:10]:  # Show first 10 errors
            print(f"     • {error}")
    
    # Step 2: Sync User Profiles
    print("\n" + "=" * 80)
    print("STEP 2: SYNCING USER PROFILES")
    print("=" * 80)
    
    preprod_cur.execute("""
        SELECT user_id, employee_id, department, job_title, 
               bio, location, phone, profile_photo,
               must_change_password, metadata, status, is_mfa_enabled,
               last_login_ip, last_login_at, failed_login_attempts, 
               locked_until, manager_id, organization_id
        FROM rbac_user_profiles
    """)
    
    profiles = preprod_cur.fetchall()
    print(f"\n📊 Found {len(profiles)} profiles in preprod")
    
    profile_created = 0
    profile_skipped = 0
    profile_errors = []
    
    for profile_data in profiles:
        user_id = profile_data[0]
        
        try:
            # Check if user exists in production
            prod_cur.execute("SELECT COUNT(*) FROM users WHERE id = %s", (user_id,))
            user_exists = prod_cur.fetchone()[0] > 0
            
            if not user_exists:
                profile_skipped += 1
                continue
            
            # Check if profile already exists
            prod_cur.execute("SELECT COUNT(*) FROM rbac_user_profiles WHERE user_id = %s", (user_id,))
            profile_exists = prod_cur.fetchone()[0] > 0
            
            if profile_exists:
                profile_skipped += 1
                continue
            
            # Insert profile
            prod_cur.execute("""
                INSERT INTO rbac_user_profiles (
                    user_id, employee_id, department, job_title,
                    bio, location, phone, profile_photo,
                    must_change_password, metadata, status, is_mfa_enabled,
                    last_login_ip, last_login_at, failed_login_attempts,
                    locked_until, manager_id, organization_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, profile_data)
            
            profile_created += 1
            
        except Exception as e:
            profile_errors.append(f"User {user_id}: {str(e)[:60]}")
    
    # Commit profile inserts
    prod_conn.commit()
    
    print(f"\n📊 PROFILE SYNC SUMMARY:")
    print(f"   ✅ Created:  {profile_created}")
    print(f"   ⏭️  Skipped:  {profile_skipped}")
    print(f"   ❌ Errors:   {len(profile_errors)}")
    
    # Close connections
    preprod_cur.close()
    prod_cur.close()
    preprod_conn.close()
    prod_conn.close()
    
    print("\n" + "=" * 80)
    print("✅ SYNC COMPLETE!")
    print("=" * 80)
    print(f"\n📈 FINAL STATS:")
    print(f"   Users Created:    {created}")
    print(f"   Profiles Created: {profile_created}")
    print(f"   Total Errors:     {len(errors) + len(profile_errors)}")
    
    if created > 0:
        print("\n✨ Next Steps:")
        print("   1. Verify users in production: SELECT COUNT(*) FROM users;")
        print("   2. Create a superuser if needed: python manage.py createsuperuser")
        print("   3. Set Railway environment variables")
        print("   4. Deploy to Railway")
    
    print("=" * 80)

except psycopg2.Error as e:
    print(f"\n❌ DATABASE ERROR: {e}")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ UNEXPECTED ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
