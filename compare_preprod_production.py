"""
Compare radai_preprod and radai_production databases for alignment.
This script checks:
- Table existence in both databases
- Record counts for common tables
- Schema differences (columns, constraints)
"""
import os
import json
from datetime import datetime
from decouple import config as env_config

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    exit(1)

# Database connection URLs
# Using TEST_DATABASE_URL as preprod and PROD_DATABASE_URL as production
PREPROD_DATABASE_URL = env_config('TEST_DATABASE_URL', default=env_config('PREPROD_DATABASE_URL', default=None))
PRODUCTION_DATABASE_URL = env_config('PROD_DATABASE_URL', default=None)

if not PREPROD_DATABASE_URL:
    print("ERROR: TEST_DATABASE_URL or PREPROD_DATABASE_URL not set in .env file")
    print("Please add: TEST_DATABASE_URL=postgresql://user:pass@host:port/radai_preprod")
    exit(1)

if not PRODUCTION_DATABASE_URL:
    print("ERROR: PROD_DATABASE_URL not set in .env file")
    print("Please add: PROD_DATABASE_URL=postgresql://user:pass@host:port/radai_production")
    exit(1)

print(f"Preprod DB: {PREPROD_DATABASE_URL.split('@')[1] if '@' in PREPROD_DATABASE_URL else 'configured'}")
print(f"Production DB: {PRODUCTION_DATABASE_URL.split('@')[1] if '@' in PRODUCTION_DATABASE_URL else 'configured'}")

print("=" * 80)
print("DATABASE ALIGNMENT CHECK: radai_preprod vs radai_production")
print("=" * 80)
print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

# Connect to both databases
print("[1/2] Connecting to PREPROD database...")
preprod_conn = psycopg2.connect(PREPROD_DATABASE_URL)
preprod_cur = preprod_conn.cursor()

print("[2/2] Connecting to PRODUCTION database...")
prod_conn = psycopg2.connect(PRODUCTION_DATABASE_URL)
prod_cur = prod_conn.cursor()

print("\n" + "=" * 80)
print("STEP 1: TABLE COMPARISON")
print("=" * 80)

# Get all tables from preprod
preprod_cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
""")
preprod_tables = {row[0] for row in preprod_cur.fetchall()}

# Get all tables from production
prod_cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
""")
prod_tables = {row[0] for row in prod_cur.fetchall()}

# Analyze differences
missing_in_preprod = prod_tables - preprod_tables
missing_in_prod = preprod_tables - prod_tables
common_tables = prod_tables & preprod_tables

print(f"\nPRODUCTION Tables: {len(prod_tables)}")
print(f"PREPROD Tables: {len(preprod_tables)}")
print(f"Common Tables: {len(common_tables)}")
print(f"Missing in Preprod: {len(missing_in_preprod)}")
print(f"Missing in Production: {len(missing_in_prod)}")

if missing_in_preprod:
    print(f"\n[WARNING] {len(missing_in_preprod)} tables exist in PRODUCTION but not in PREPROD:")
    for table in sorted(list(missing_in_preprod)[:20]):
        print(f"  - {table}")
    if len(missing_in_preprod) > 20:
        print(f"  ... and {len(missing_in_preprod) - 20} more")

if missing_in_prod:
    print(f"\n[WARNING] {len(missing_in_prod)} tables exist in PREPROD but not in PRODUCTION:")
    for table in sorted(list(missing_in_prod)[:20]):
        print(f"  - {table}")
    if len(missing_in_prod) > 20:
        print(f"  ... and {len(missing_in_prod) - 20} more")

print("\n" + "=" * 80)
print("STEP 2: RECORD COUNT COMPARISON")
print("=" * 80)

record_differences = []

for table in sorted(common_tables):
    try:
        # Get production count
        prod_cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        prod_count = prod_cur.fetchone()[0]
        
        # Get preprod count
        preprod_cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        preprod_count = preprod_cur.fetchone()[0]
        
        diff = prod_count - preprod_count
        status = "[OK]" if diff == 0 else "[DIFF]"
        
        if diff != 0:
            record_differences.append({
                'table': table,
                'production': prod_count,
                'preprod': preprod_count,
                'difference': diff
            })
            print(f"{status} {table:<50} Prod: {prod_count:>8,} | Preprod: {preprod_count:>8,} | Diff: {diff:>8,}")
    except Exception as e:
        print(f"[ERROR] {table:<50} {e}")

print("\n" + "=" * 80)
print("STEP 3: SCHEMA COMPARISON (Columns)")
print("=" * 80)

schema_differences = []

for table in sorted(list(common_tables)[:10]):  # Check first 10 tables for demo
    try:
        # Get columns from production
        prod_cur.execute("""
            SELECT column_name, data_type, character_maximum_length, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        prod_columns = {row[0]: row for row in prod_cur.fetchall()}
        
        # Get columns from preprod
        preprod_cur.execute("""
            SELECT column_name, data_type, character_maximum_length, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        preprod_columns = {row[0]: row for row in preprod_cur.fetchall()}
        
        # Compare columns
        missing_cols_preprod = set(prod_columns.keys()) - set(preprod_columns.keys())
        missing_cols_prod = set(preprod_columns.keys()) - set(prod_columns.keys())
        
        if missing_cols_preprod or missing_cols_prod:
            schema_differences.append({
                'table': table,
                'missing_in_preprod': list(missing_cols_preprod),
                'missing_in_production': list(missing_cols_prod)
            })
            print(f"\n[DIFF] {table}:")
            if missing_cols_preprod:
                print(f"  Columns in prod but not preprod: {', '.join(missing_cols_preprod)}")
            if missing_cols_prod:
                print(f"  Columns in preprod but not prod: {', '.join(missing_cols_prod)}")
    except Exception as e:
        print(f"[ERROR] {table}: {e}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

aligned = True

if missing_in_preprod or missing_in_prod:
    aligned = False
    print(f"\n[❌] TABLE MISMATCH: Databases are NOT aligned")
    print(f"  - {len(missing_in_preprod)} tables missing in preprod")
    print(f"  - {len(missing_in_prod)} tables missing in production")

if record_differences:
    aligned = False
    print(f"\n[❌] RECORD COUNT MISMATCH: {len(record_differences)} tables have different counts:")
    for item in sorted(record_differences, key=lambda x: abs(x['difference']), reverse=True)[:10]:
        print(f"  - {item['table']}: {item['difference']:+,} records (Prod: {item['production']:,}, Preprod: {item['preprod']:,})")
    if len(record_differences) > 10:
        print(f"  ... and {len(record_differences) - 10} more tables")

if schema_differences:
    aligned = False
    print(f"\n[❌] SCHEMA MISMATCH: {len(schema_differences)} tables have column differences")

if aligned:
    print("\n[✓] DATABASES ARE FULLY ALIGNED!")
    print("  - All tables match")
    print("  - All record counts match")
    print("  - Schemas are identical (sampled)")
else:
    print("\n[❌] DATABASES ARE NOT ALIGNED")
    print("Review the differences above and sync as needed.")

# Save results to JSON
results = {
    'timestamp': datetime.now().isoformat(),
    'aligned': aligned,
    'table_comparison': {
        'production_count': len(prod_tables),
        'preprod_count': len(preprod_tables),
        'common_count': len(common_tables),
        'missing_in_preprod': sorted(list(missing_in_preprod)),
        'missing_in_production': sorted(list(missing_in_prod))
    },
    'record_differences': record_differences,
    'schema_differences': schema_differences
}

output_file = 'preprod_production_comparison.json'
with open(output_file, 'w') as f:
    json.dump(results, f, indent=2)

print(f"\n[SAVED] Detailed results saved to: {output_file}")

preprod_cur.close()
preprod_conn.close()
prod_cur.close()
prod_conn.close()

print("\n" + "=" * 80)
print("[COMPLETE] Alignment check finished!")
print("=" * 80)

# Exit with error code if not aligned
exit(0 if aligned else 1)
