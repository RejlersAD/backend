"""
Check PRODUCTION procurement data - Purchase Orders and Purchase Requisitions
Connect directly to Railway production database
"""
import psycopg2
import os
from datetime import datetime

print("=" * 80)
print("PRODUCTION DATABASE - PROCUREMENT DATA CHECK")
print("=" * 80)

# Railway production database connection
# You need to get these from Railway dashboard
PRODUCTION_DB = {
    'dbname': 'railway',
    'user': 'postgres',
    'password': os.environ.get('RAILWAY_DB_PASSWORD', ''),  # Set this in environment
    'host': os.environ.get('RAILWAY_DB_HOST', ''),  # e.g., postgres.railway.internal
    'port': os.environ.get('RAILWAY_DB_PORT', '5432'),
}

# Ask user for connection details if not in environment
if not PRODUCTION_DB['password']:
    print("\n⚠️  Production database credentials not set in environment.")
    print("\nTo check production database, you need:")
    print("  1. Go to Railway dashboard: https://railway.app/")
    print("  2. Select your project -> Backend service -> Variables tab")
    print("  3. Look for DATABASE_URL or:")
    print("     - PGHOST")
    print("     - PGPORT")
    print("     - PGUSER")
    print("     - PGPASSWORD")
    print("     - PGDATABASE")
    print("\n" + "=" * 80)
    exit(0)

try:
    print("\n🔌 Connecting to production database...")
    print(f"   Host: {PRODUCTION_DB['host']}")
    print(f"   Database: {PRODUCTION_DB['dbname']}")
    print(f"   User: {PRODUCTION_DB['user']}")
    
    conn = psycopg2.connect(**PRODUCTION_DB)
    cursor = conn.cursor()
    
    print("✅ Connected to production database!\n")
    
    # Check Purchase Requisitions
    cursor.execute("SELECT COUNT(*) FROM procurement_purchaserequisition")
    pr_count = cursor.fetchone()[0]
    print(f"📋 Purchase Requisitions: {pr_count} records")
    
    if pr_count > 0:
        cursor.execute("""
            SELECT pr_number, status, created_at, requester_id
            FROM procurement_purchaserequisition
            ORDER BY created_at DESC
            LIMIT 5
        """)
        print("\n  Recent PRs:")
        for row in cursor.fetchall():
            pr_number, status, created_at, requester_id = row
            print(f"    - PR #{pr_number} | Status: {status} | Created: {created_at.strftime('%Y-%m-%d %H:%M')}")
    else:
        print("  ❌ No Purchase Requisitions found in PRODUCTION database")
    
    # Check Purchase Orders
    cursor.execute("SELECT COUNT(*) FROM procurement_purchaseorder")
    po_count = cursor.fetchone()[0]
    print(f"\n📦 Purchase Orders: {po_count} records")
    
    if po_count > 0:
        cursor.execute("""
            SELECT po_number, status, created_at, vendor_id, total_amount
            FROM procurement_purchaseorder
            ORDER BY created_at DESC
            LIMIT 5
        """)
        print("\n  Recent POs:")
        for row in cursor.fetchall():
            po_number, status, created_at, vendor_id, total_amount = row
            print(f"    - PO #{po_number} | Status: {status} | Created: {created_at.strftime('%Y-%m-%d %H:%M')}")
            print(f"      Vendor ID: {vendor_id} | Total: {total_amount}")
    else:
        print("  ❌ No Purchase Orders found in PRODUCTION database")
    
    # Check if tables exist
    print("\n🔍 Checking table structure...")
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema='public' 
        AND table_name LIKE 'procurement_%'
        ORDER BY table_name
    """)
    tables = cursor.fetchall()
    print(f"\n  Procurement tables in production: {len(tables)}")
    for table in tables:
        print(f"    - {table[0]}")
    
    # Check migrations
    print("\n🔄 Checking applied migrations...")
    cursor.execute("""
        SELECT name 
        FROM django_migrations 
        WHERE app='procurement'
        ORDER BY applied DESC
        LIMIT 5
    """)
    migrations = cursor.fetchall()
    print(f"\n  Recent procurement migrations: {len(migrations)}")
    for mig in migrations:
        print(f"    - {mig[0]}")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Purchase Requisitions: {pr_count}")
    print(f"  Purchase Orders: {po_count}")
    
    if pr_count == 0 and po_count == 0:
        print("\n⚠️  WARNING: No procurement data in PRODUCTION!")
        print("  This means data is NOT syncing from local to production.")
        print("\n  Possible reasons:")
        print("    1. Migrations not applied in production")
        print("    2. Database is completely separate (not shared)")
        print("    3. Data was created in local DB but production has its own DB")
    
    print("=" * 80)

except psycopg2.OperationalError as e:
    print(f"\n❌ Connection Error: {e}")
    print("\n💡 Tip: Get production DB credentials from Railway:")
    print("   railway.app → Your Project → Backend → Variables → DATABASE_URL")
except Exception as e:
    print(f"\n❌ Error: {e}")
