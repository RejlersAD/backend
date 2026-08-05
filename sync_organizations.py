import psycopg2
from psycopg2.extras import Json
import sys

PREPROD_DB = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
PROD_DB = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"

print("=" * 80)
print("SYNCING ORGANIZATIONS")
print("=" * 80)

try:
    preprod_conn = psycopg2.connect(PREPROD_DB)
    prod_conn = psycopg2.connect(PROD_DB)
    
    preprod_cur = preprod_conn.cursor()
    prod_cur = prod_conn.cursor()
    
    # Get organizations from preprod
    preprod_cur.execute("""
        SELECT created_at, updated_at, id, name, code, description,
               is_active, primary_contact_name, primary_contact_email, primary_contact_phone,
               address_line1, address_line2, city, country, postal_code,
               s3_bucket_name, s3_region
        FROM rbac_organizations
    """)
    
    orgs = preprod_cur.fetchall()
    print(f"\n📊 Found {len(orgs)} organizations in preprod\n")
    
    created = 0
    skipped = 0
    
    for org_data in orgs:
        org_id = org_data[2]  # id is at index 2
        org_name = org_data[3]  # name is at index 3
        
        try:
            # Check if org exists
            prod_cur.execute("SELECT COUNT(*) FROM rbac_organizations WHERE id = %s", (org_id,))
            exists = prod_cur.fetchone()[0] > 0
            
            if exists:
                print(f"   ⏭️  {org_name}")
                skipped += 1
                continue
            
            # Insert organization
            prod_cur.execute("""
                INSERT INTO rbac_organizations (
                    created_at, updated_at, id, name, code, description,
                    is_active, primary_contact_name, primary_contact_email, primary_contact_phone,
                    address_line1, address_line2, city, country, postal_code,
                    s3_bucket_name, s3_region
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, org_data)
            
            print(f"   ✅ {org_name}")
            created += 1
            
        except Exception as e:
            print(f"   ❌ {org_name}: {str(e)[:60]}")
    
    prod_conn.commit()
    
    print(f"\n📊 ORGANIZATION SYNC SUMMARY:")
    print(f"   ✅ Created:  {created}")
    print(f"   ⏭️  Skipped:  {skipped}")
    
    preprod_cur.close()
    prod_cur.close()
    preprod_conn.close()
    prod_conn.close()
    
    print("\n" + "=" * 80)
    print("✅ ORGANIZATION SYNC COMPLETE!")
    print("=" * 80)
    print("\nNow run: python sync_profiles_only.py")
    print("=" * 80)

except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
