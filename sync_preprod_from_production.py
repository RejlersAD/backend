"""
Sync Preprod Database to Match Production Database
Soft-coded, flexible synchronization script that:
- Discovers missing tables automatically
- Handles foreign key dependencies
- Syncs data incrementally
- Generates migration commands for missing tables
- Provides detailed progress tracking
"""
import os
import json
import sys
from datetime import datetime
from decouple import config as env_config
from collections import defaultdict

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    exit(1)

# ===== CONFIGURATION =====
# Source and target databases (soft-coded from .env)
SOURCE_DB_URL = env_config('PROD_DATABASE_URL', default=None)  # Production (source)
TARGET_DB_URL = env_config('TEST_DATABASE_URL', default=env_config('PREPROD_DATABASE_URL', default=None))  # Preprod (target)

# Sync options
BATCH_SIZE = 1000  # Records per batch
DRY_RUN = env_config('DRY_RUN', default=False, cast=bool)  # Set to True to preview changes only
SKIP_TABLES = []  # Add table names to skip (e.g., ['django_session', 'auth_user'])
SYNC_DATA_ONLY = True  # If True, only sync data for existing tables (skip schema)

# ===== VALIDATION =====
if not SOURCE_DB_URL:
    print("ERROR: PROD_DATABASE_URL not set in .env file")
    exit(1)

if not TARGET_DB_URL:
    print("ERROR: TEST_DATABASE_URL or PREPROD_DATABASE_URL not set in .env file")
    exit(1)

print("=" * 80)
print("PREPROD SYNC: Production → Preprod")
print("=" * 80)
print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Mode: {'DRY RUN (no changes will be made)' if DRY_RUN else 'LIVE SYNC'}")
print(f"Source: {SOURCE_DB_URL.split('@')[1] if '@' in SOURCE_DB_URL else 'Production'}")
print(f"Target: {TARGET_DB_URL.split('@')[1] if '@' in TARGET_DB_URL else 'Preprod'}")
print()

if not DRY_RUN:
    response = input("⚠️  This will modify the PREPROD database. Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Sync cancelled.")
        exit(0)

# ===== CONNECT TO DATABASES =====
print("\n[1/5] Connecting to databases...")
try:
    source_conn = psycopg2.connect(SOURCE_DB_URL)
    source_cur = source_conn.cursor(cursor_factory=RealDictCursor)
    print("  ✓ Connected to source (production)")
except Exception as e:
    print(f"  ✗ Failed to connect to source: {e}")
    exit(1)

try:
    target_conn = psycopg2.connect(TARGET_DB_URL)
    target_cur = target_conn.cursor(cursor_factory=RealDictCursor)
    print("  ✓ Connected to target (preprod)")
except Exception as e:
    print(f"  ✗ Failed to connect to target: {e}")
    exit(1)

# ===== DISCOVER TABLES =====
print("\n[2/5] Discovering table differences...")

# Get all tables from source
source_cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
""")
source_tables = {row['table_name'] for row in source_cur.fetchall()}

# Get all tables from target
target_cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
""")
target_tables = {row['table_name'] for row in target_cur.fetchall()}

# Calculate differences
missing_tables = source_tables - target_tables - set(SKIP_TABLES)
common_tables = (source_tables & target_tables) - set(SKIP_TABLES)

print(f"  Source tables: {len(source_tables)}")
print(f"  Target tables: {len(target_tables)}")
print(f"  Missing in target: {len(missing_tables)}")
print(f"  Common tables: {len(common_tables)}")

# ===== GET FOREIGN KEY DEPENDENCIES =====
print("\n[3/5] Analyzing foreign key dependencies...")

def get_table_dependencies(cursor, tables):
    """Get foreign key dependencies for tables"""
    cursor.execute("""
        SELECT
            tc.table_name as table_name,
            ccu.table_name AS referenced_table
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
            AND tc.table_name = ANY(%s);
    """, (list(tables),))
    
    dependencies = defaultdict(set)
    for row in cursor.fetchall():
        dependencies[row['table_name']].add(row['referenced_table'])
    
    return dependencies

# Topological sort to determine sync order
def topological_sort(tables, dependencies):
    """Sort tables by dependency order"""
    sorted_tables = []
    visited = set()
    temp_visited = set()
    
    def visit(table):
        if table in temp_visited:
            return  # Circular dependency, skip
        if table in visited:
            return
        
        temp_visited.add(table)
        for dep in dependencies.get(table, []):
            if dep in tables:
                visit(dep)
        temp_visited.remove(table)
        visited.add(table)
        sorted_tables.append(table)
    
    for table in tables:
        if table not in visited:
            visit(table)
    
    return sorted_tables

dependencies = get_table_dependencies(source_cur, common_tables)
sync_order = topological_sort(common_tables, dependencies)

print(f"  Analyzed dependencies for {len(common_tables)} tables")
print(f"  Determined optimal sync order")

# ===== HANDLE MISSING TABLES =====
print("\n[4/5] Handling missing tables...")

if missing_tables:
    print(f"\n  ⚠️  {len(missing_tables)} tables missing in preprod")
    print(f"  These need to be created via Django migrations:\n")
    
    migration_commands = []
    migration_commands.append("cd backend")
    migration_commands.append("python manage.py makemigrations")
    migration_commands.append("python manage.py migrate --database=default")
    
    print("  Run these commands to create missing tables:")
    for cmd in migration_commands:
        print(f"    {cmd}")
    
    print(f"\n  Missing tables (first 20):")
    for table in sorted(list(missing_tables)[:20]):
        print(f"    - {table}")
    if len(missing_tables) > 20:
        print(f"    ... and {len(missing_tables) - 20} more")
    
    if not SYNC_DATA_ONLY:
        print("\n  ❌ Cannot proceed with data sync until tables are created.")
        print("  Options:")
        print("    1. Run migrations above, then re-run this script")
        print("    2. Set SYNC_DATA_ONLY=True to sync only existing tables")
        
        # Save missing tables list
        with open('missing_tables.json', 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'count': len(missing_tables),
                'tables': sorted(list(missing_tables))
            }, f, indent=2)
        print(f"\n  [SAVED] Missing tables list: missing_tables.json")

# ===== SYNC DATA =====
print("\n[5/5] Syncing data for common tables...")

sync_stats = {
    'tables_synced': 0,
    'records_synced': 0,
    'tables_skipped': 0,
    'errors': []
}

for idx, table in enumerate(sync_order, 1):
    try:
        # Get record counts
        source_cur.execute(f'SELECT COUNT(*) as count FROM "{table}"')
        source_count = source_cur.fetchone()['count']
        
        target_cur.execute(f'SELECT COUNT(*) as count FROM "{table}"')
        target_count = target_cur.fetchone()['count']
        
        diff = source_count - target_count
        
        if diff <= 0:
            print(f"  [{idx}/{len(sync_order)}] ✓ {table:<50} (up to date)")
            continue
        
        print(f"  [{idx}/{len(sync_order)}] ⟳ {table:<50} (+{diff:,} records)", end='', flush=True)
        
        if DRY_RUN:
            print(" [DRY RUN]")
            sync_stats['tables_skipped'] += 1
            continue
        
        # Get primary key column(s) - improved detection
        source_cur.execute("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = ('public.' || %s)::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)
        """, (table,))
        pk_result = source_cur.fetchall()
        pk_columns = [row['attname'] if isinstance(row, dict) else row[0] for row in pk_result]
        
        # Fallback: try common PK column names
        if not pk_columns:
            source_cur.execute(f"""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
                AND column_name IN ('id', 'uuid', table_name || '_id')
                LIMIT 1
            """, (table,))
            fallback_pk = source_cur.fetchone()
            if fallback_pk:
                pk_columns = [fallback_pk['column_name'] if isinstance(fallback_pk, dict) else fallback_pk[0]]
        
        if not pk_columns:
            print(" [NO PK, SKIPPED]")
            sync_stats['tables_skipped'] += 1
            continue
        
        # Get existing IDs in target
        if pk_columns:
            pk_col = pk_columns[0]  # Use first PK column
            target_cur.execute(f'SELECT "{pk_col}" FROM "{table}"')
            existing_ids = {row[pk_col] for row in target_cur.fetchall()}
        else:
            existing_ids = set()
        
        # Fetch missing records from source
        source_cur.execute(f'SELECT * FROM "{table}"')
        source_records = source_cur.fetchall()
        
        # Filter out existing records
        new_records = [r for r in source_records if r.get(pk_col) not in existing_ids]
        
        if new_records:
            # Get column names
            columns = list(new_records[0].keys())
            columns_str = ', '.join([f'"{col}"' for col in columns])
            placeholders = ', '.join(['%s'] * len(columns))
            
            # Insert in batches
            for i in range(0, len(new_records), BATCH_SIZE):
                batch = new_records[i:i + BATCH_SIZE]
                values = [tuple(record[col] for col in columns) for record in batch]
                
                insert_query = f'INSERT INTO "{table}" ({columns_str}) VALUES ({placeholders})'
                target_cur.executemany(insert_query, values)
                target_conn.commit()
            
            sync_stats['records_synced'] += len(new_records)
            sync_stats['tables_synced'] += 1
            print(f" [SYNCED {len(new_records):,}]")
        else:
            print(" [UP TO DATE]")
        
    except Exception as e:
        print(f" [ERROR: {str(e)[:50]}]")
        sync_stats['errors'].append({'table': table, 'error': str(e)})
        target_conn.rollback()

# ===== SUMMARY =====
print("\n" + "=" * 80)
print("SYNC SUMMARY")
print("=" * 80)

print(f"\n📊 Statistics:")
print(f"  Tables synced: {sync_stats['tables_synced']}")
print(f"  Records synced: {sync_stats['records_synced']:,}")
print(f"  Tables skipped: {sync_stats['tables_skipped']}")
print(f"  Errors: {len(sync_stats['errors'])}")

if sync_stats['errors']:
    print(f"\n❌ Errors encountered:")
    for err in sync_stats['errors'][:10]:
        print(f"  - {err['table']}: {err['error'][:80]}")
    if len(sync_stats['errors']) > 10:
        print(f"  ... and {len(sync_stats['errors']) - 10} more")

if missing_tables and not SYNC_DATA_ONLY:
    print(f"\n⚠️  Next steps:")
    print(f"  1. Run migrations to create {len(missing_tables)} missing tables")
    print(f"  2. Re-run this script to sync data for new tables")

# Save sync report
report = {
    'timestamp': datetime.now().isoformat(),
    'mode': 'dry_run' if DRY_RUN else 'live',
    'stats': sync_stats,
    'missing_tables_count': len(missing_tables),
    'common_tables_count': len(common_tables)
}

with open('sync_report.json', 'w') as f:
    json.dump(report, f, indent=2)

print(f"\n[SAVED] Sync report: sync_report.json")

# Cleanup
source_cur.close()
source_conn.close()
target_cur.close()
target_conn.close()

print("\n" + "=" * 80)
print("✓ SYNC COMPLETE")
print("=" * 80)

exit(0 if not sync_stats['errors'] else 1)
