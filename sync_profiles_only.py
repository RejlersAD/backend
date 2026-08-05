import psycopg2
from psycopg2.extras import Json
import sys

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("=" * 80)
print("SYNCING USER PROFILES (FIXED VERSION)")
print("=" * 80)

try:
    preprod_conn = psycopg2.connect(PREPROD_DB)
    prod_conn = psycopg2.connect(PROD_DB)
    
    preprod_cur = preprod_conn.cursor()
    prod_cur = prod_conn.cursor()
    
    # Get profiles from preprod with correct column order
    preprod_cur.execute("""
        SELECT created_at, updated_at, id, status, is_mfa_enabled, employee_id,
               department, job_title, last_login_ip, last_login_at, failed_login_attempts,
               locked_until, is_deleted, deleted_at, deleted_by_id, manager_id,
               organization_id, user_id, metadata, must_change_password,
               bio, location, phone, profile_photo
        FROM rbac_user_profiles
    """)
    
    profiles = preprod_cur.fetchall()
    print(f"\n📊 Found {len(profiles)} profiles in preprod\n")
    
    created = 0
    skipped = 0
    errors = []
    
    for profile_data in profiles:
        user_id = profile_data[17]  # user_id is at index 17
        
        try:
            # Check if user exists in production
            prod_cur.execute("SELECT COUNT(*) FROM users WHERE id = %s", (user_id,))
            user_exists = prod_cur.fetchone()[0] > 0
            
            if not user_exists:
                skipped += 1
                continue
            
            # Check if profile already exists
            prod_cur.execute("SELECT COUNT(*) FROM rbac_user_profiles WHERE user_id = %s", (user_id,))
            profile_exists = prod_cur.fetchone()[0] > 0
            
            if profile_exists:
                skipped += 1
                continue
            
            # Insert profile with all columns - convert metadata dict to Json
            profile_data_fixed = list(profile_data)
            # metadata is at index 18
            if profile_data_fixed[18] is not None:
                profile_data_fixed[18] = Json(profile_data_fixed[18])
            
            prod_cur.execute("""
                INSERT INTO rbac_user_profiles (
                    created_at, updated_at, id, status, is_mfa_enabled, employee_id,
                    department, job_title, last_login_ip, last_login_at, failed_login_attempts,
                    locked_until, is_deleted, deleted_at, deleted_by_id, manager_id,
                    organization_id, user_id, metadata, must_change_password,
                    bio, location, phone, profile_photo
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, tuple(profile_data_fixed))
            
            created += 1
            if created % 50 == 0:
                print(f"   ✅ {created} profiles synced...")
            
        except Exception as e:
            error_msg = f"User {user_id}: {str(e)[:80]}"
            errors.append(error_msg)
            if len(errors) <= 5:  # Show first 5 errors
                print(f"   ❌ {error_msg}")
    
    # Commit
    prod_conn.commit()
    
    print(f"\n📊 PROFILE SYNC SUMMARY:")
    print(f"   ✅ Created:  {created}")
    print(f"   ⏭️  Skipped:  {skipped}")
    print(f"   ❌ Errors:   {len(errors)}")
    
    preprod_cur.close()
    prod_cur.close()
    preprod_conn.close()
    prod_conn.close()
    
    print("\n" + "=" * 80)
    print("✅ PROFILE SYNC COMPLETE!")
    print("=" * 80)

except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
