"""
Quick script to verify which tables exist in production database.
Helps identify which tables can be synced.
"""
import os
from decouple import config as env_config

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    exit(1)

# Get production database URL from .env
PROD_DATABASE_URL = env_config('PROD_DATABASE_URL', default=None)

if not PROD_DATABASE_URL:
    print("ERROR: PROD_DATABASE_URL not set in .env file")
    exit(1)

print(f"Connecting to production database...")
print(f"Host: {PROD_DATABASE_URL.split('@')[1].split('/')[0]}")
print()

try:
    conn = psycopg2.connect(PROD_DATABASE_URL)
    cursor = conn.cursor()
    
    # Get all tables in production
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    
    tables = cursor.fetchall()
    
    print("=" * 80)
    print("PRODUCTION DATABASE TABLES")
    print("=" * 80)
    print(f"\nTotal tables: {len(tables)}\n")
    
    # Tables configured for sync
    sync_tables = [
        'rbac_userprofile',
        'timesheet_biometricusermaster',
        'timesheet_timesheetevent',
        'timesheet_dailyattendancesummary',
        'payroll_employeeleaverecord',
        'payroll_leaverequest',
    ]
    
    existing_tables = [t[0] for t in tables]
    
    # Check which sync tables exist
    print("Configured Sync Tables Status:")
    print("-" * 80)
    for table in sync_tables:
        status = "✅ EXISTS" if table in existing_tables else "❌ MISSING"
        print(f"  {status}  {table}")
    
    print("\n")
    print("All Production Tables:")
    print("-" * 80)
    for i, (table,) in enumerate(tables, 1):
        marker = "  →" if table in sync_tables else "   "
        print(f"{marker} {i:3d}. {table}")
    
    print("\n")
    print("=" * 80)
    print("✅ Connection successful!")
    print("=" * 80)
    
    cursor.close()
    conn.close()
    
except psycopg2.OperationalError as e:
    print(f"❌ CONNECTION FAILED: {e}")
    print("\nPossible issues:")
    print("  1. Wrong credentials in PROD_DATABASE_URL")
    print("  2. Network/firewall blocking connection")
    print("  3. Database server is down")
    exit(1)
except Exception as e:
    print(f"❌ ERROR: {e}")
    exit(1)
