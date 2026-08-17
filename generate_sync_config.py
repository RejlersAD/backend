"""
Auto-generate sync configuration for ALL production tables.
Discovers tables dynamically and creates the configuration.
"""
import os
from decouple import config as env_config

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    exit(1)

PROD_DATABASE_URL = env_config('PROD_DATABASE_URL', default=None)

if not PROD_DATABASE_URL:
    print("ERROR: PROD_DATABASE_URL not set in .env file")
    exit(1)

print("Connecting to production database...")
conn = psycopg2.connect(PROD_DATABASE_URL)
cur = conn.cursor()

# Get all tables with their columns
cur.execute("""
    SELECT 
        t.table_name,
        array_agg(c.column_name::text ORDER BY c.ordinal_position) as columns,
        array_agg(c.data_type::text ORDER BY c.ordinal_position) as types
    FROM information_schema.tables t
    JOIN information_schema.columns c ON t.table_name = c.table_name
    WHERE t.table_schema = 'public' 
    AND t.table_type = 'BASE TABLE'
    AND c.table_schema = 'public'
    GROUP BY t.table_name
    ORDER BY t.table_name;
""")

tables = cur.fetchall()

print(f"\n✅ Found {len(tables)} tables in production\n")

# Generate configuration
config_lines = []
priority = 100

for table_name, columns, types in tables:
    columns_list = columns
    types_list = types
    
    # Detect timestamp column
    timestamp_col = None
    for col, typ in zip(columns_list, types_list):
        if col in ['created_at', 'updated_at', 'timestamp', 'event_time', 'date', 'created', 'modified']:
            if 'timestamp' in typ or 'date' in typ:
                timestamp_col = col
                break
    
    # Detect primary key (assume 'id' for now)
    pk_col = 'id' if 'id' in columns_list else columns_list[0]
    order_col = timestamp_col if timestamp_col else pk_col
    
    ts_value = "None" if not timestamp_col else f"'{timestamp_col}'"
    
    config_lines.append(f"    '{table_name}': {{")
    config_lines.append(f"        'description':    '{table_name.replace('_', ' ').title()}',")
    config_lines.append(f"        'timestamp_col':  {ts_value},")
    config_lines.append(f"        'pk_col':         '{pk_col}',")
    config_lines.append(f"        'order_col':      '{order_col}',")
    config_lines.append(f"        'priority':       {priority},")
    config_lines.append(f"    }},")
    
    priority += 1

# Save to file
output = "# Auto-generated table configuration\nSYNC_TABLE_CONFIG = {\n" + "\n".join(config_lines) + "\n}\n"

with open('sync_config_all_tables.py', 'w') as f:
    f.write(output)

print(f"✅ Generated configuration for {len(tables)} tables")
print(f"💾 Saved to: sync_config_all_tables.py")
print(f"\nYou can copy this configuration to:")
print(f"  backend/apps/timesheet/management/commands/sync_from_production.py")

cur.close()
conn.close()
