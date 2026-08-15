"""
Auto-sync ALL tables from production to local/staging database.
This discovers all tables dynamically and syncs them.
"""
import os
import json
from decouple import config as env_config

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    exit(1)

# Get database URLs from .env
PROD_DATABASE_URL = env_config('PROD_DATABASE_URL', default=None)
# Use TEST_DATABASE_URL (staging) as fallback if LOCAL_DATABASE_URL doesn't work
LOCAL_DATABASE_URL = env_config('TEST_DATABASE_URL', default=env_config('LOCAL_DATABASE_URL', default=None))

if not PROD_DATABASE_URL:
    print("ERROR: PROD_DATABASE_URL not set in .env file")
    exit(1)

if not LOCAL_DATABASE_URL:
    print("ERROR: Neither TEST_DATABASE_URL nor LOCAL_DATABASE_URL is set in .env file") 
    exit(1)

print("=" * 80)
print("DATABASE TABLE COMPARISON")
print("=" * 80)
print()

# Connect to production
print("[1/2] Connecting to PRODUCTION database...")
prod_conn = psycopg2.connect(PROD_DATABASE_URL)
prod_cur = prod_conn.cursor()

# Connect to local
print("[2/2] Connecting to LOCAL/STAGING database...")
local_conn = psycopg2.connect(LOCAL_DATABASE_URL)
local_cur = local_conn.cursor()

# Get all tables from production
prod_cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
""")
prod_tables = {row[0] for row in prod_cur.fetchall()}

# Get all tables from local
local_cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
""")
local_tables = {row[0] for row in local_cur.fetchall()}

# Analyze differences
missing_in_local = prod_tables - local_tables
extra_in_local = local_tables - prod_tables
common_tables = prod_tables & local_tables

print(f"\nPRODUCTION Tables: {len(prod_tables)}")
print(f"LOCAL/STAGING Tables: {len(local_tables)}")
print(f"Common Tables: {len(common_tables)}")
print(f"Missing in Local: {len(missing_in_local)}")
print(f"Extra in Local: {len(extra_in_local)}")

print("\n" + "=" * 80)
print("RECORD COUNT COMPARISON (Common Tables)")
print("=" * 80)

differences = []

for table in sorted(common_tables):
    try:
        # Get production count
        prod_cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        prod_count = prod_cur.fetchone()[0]
        
        # Get local count
        local_cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        local_count = local_cur.fetchone()[0]
        
        diff = prod_count - local_count
        status = "[OK]" if diff == 0 else "[DIFF]"
        
        if diff != 0:
            differences.append({
                'table': table,
                'production': prod_count,
                'local': local_count,
                'difference': diff
            })
            print(f"{status} {table:<50} Prod: {prod_count:>8,} | Local: {local_count:>8,} | Diff: {diff:>8,}")
    except Exception as e:
        print(f"[ERROR] {table:<50} {e}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

if differences:
    print(f"\n[!] {len(differences)} tables have different record counts:\n")
    for item in differences:
        print(f"  - {item['table']}: {item['difference']:+,} records")
else:
    print("\n[OK] All common tables have matching record counts!")

if missing_in_local:
    print(f"\n[!] {len(missing_in_local)} tables exist in PRODUCTION but not in LOCAL:")
    for table in sorted(missing_in_local)[:20]:
        print(f"  - {table}")
    if len(missing_in_local) > 20:
        print(f"  ... and {len(missing_in_local) - 20} more")

if extra_in_local:
    print(f"\n[+] {len(extra_in_local)} tables exist in LOCAL but not in PRODUCTION:")
    for table in sorted(extra_in_local)[:20]:
        print(f"  - {table}")
    if len(extra_in_local) > 20:
        print(f"  ... and {len(extra_in_local) - 20} more")

# Save results to JSON
results = {
    'production_count': len(prod_tables),
    'local_count': len(local_tables),
    'common_count': len(common_tables),
    'missing_in_local': list(missing_in_local),
    'extra_in_local': list(extra_in_local),
    'differences': differences
}

with open('database_comparison.json', 'w') as f:
    json.dump(results, f, indent=2)

print(f"\n[SAVED] Detailed results saved to: database_comparison.json")

prod_cur.close()
prod_conn.close()
local_cur.close()
local_conn.close()

print("\n" + "=" * 80)
print("[OK] Comparison complete!")
print("=" * 80)
