#!/usr/bin/env python
"""
Fix migration conflict in Docker database
"""
import os
import sys
import django
import psycopg2

# Database connection from .env
from decouple import config

DATABASE_URL = config('DATABASE_URL', default='postgresql://postgres:postgres@localhost:5432/radai_db')

# Replace Docker hostname with localhost for local access
DATABASE_URL = DATABASE_URL.replace('@aiflow_db:', '@localhost:')

# Parse DATABASE_URL
if DATABASE_URL.startswith('postgresql://'):
    # Format: postgresql://user:password@host:port/dbname
    parts = DATABASE_URL.replace('postgresql://', '').split('@')
    user_pass = parts[0].split(':')
    host_port_db = parts[1].split('/')
    host_port = host_port_db[0].split(':')
    
    db_config = {
        'dbname': host_port_db[1].split('?')[0],
        'user': user_pass[0],
        'password': user_pass[1] if len(user_pass) > 1 else '',
        'host': host_port[0],
        'port': host_port[1] if len(host_port) > 1 else '5432'
    }
else:
    # Fallback to individual vars
    db_config = {
        'dbname': config('DB_NAME', default='radai_db'),
        'user': config('DB_USER', default='postgres'),
        'password': config('DB_PASSWORD', default='postgres'),
        'host': config('DB_HOST', default='localhost'),
        'port': config('DB_PORT', default='5432')
    }

try:
    # Connect to database
    conn = psycopg2.connect(**db_config)
    conn.autocommit = True
    cursor = conn.cursor()
    
    print(f"✅ Connected to database: {db_config['dbname']}")
    
    # Check if migration table exists
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'django_migrations'
        );
    """)
    table_exists = cursor.fetchone()[0]
    
    if not table_exists:
        print("❌ django_migrations table does not exist!")
        sys.exit(1)
    
    # Check current migration status
    cursor.execute("""
        SELECT app, name, applied 
        FROM django_migrations 
        WHERE app = 'process_datasheet' 
        AND name IN ('0005_merge_migrations', '0006_control_valve_delta_p')
        ORDER BY applied;
    """)
    
    migrations = cursor.fetchall()
    print(f"\n📋 Current migrations for process_datasheet:")
    for app, name, applied in migrations:
        print(f"  - {name}: {applied}")
    
    # Fix: Delete 0006 entry and re-add it after 0005
    print("\n🔧 Fixing migration order...")
    
    # Delete the problematic migration
    cursor.execute("""
        DELETE FROM django_migrations 
        WHERE app = 'process_datasheet' 
        AND name = '0006_control_valve_delta_p';
    """)
    print("  ✅ Deleted 0006_control_valve_delta_p")
    
    # Ensure 0005 exists
    cursor.execute("""
        INSERT INTO django_migrations (app, name, applied)
        VALUES ('process_datasheet', '0005_merge_migrations', NOW())
        ON CONFLICT DO NOTHING;
    """)
    print("  ✅ Ensured 0005_merge_migrations exists")
    
    # Re-add 0006 with correct timestamp
    cursor.execute("""
        INSERT INTO django_migrations (app, name, applied)
        VALUES ('process_datasheet', '0006_control_valve_delta_p', NOW())
        ON CONFLICT DO NOTHING;
    """)
    print("  ✅ Re-added 0006_control_valve_delta_p")
    
    print("\n✅ Migration conflict fixed successfully!")
    
    # Show final state
    cursor.execute("""
        SELECT name, applied 
        FROM django_migrations 
        WHERE app = 'process_datasheet' 
        ORDER BY applied DESC
        LIMIT 10;
    """)
    
    print("\n📋 Latest migrations:")
    for name, applied in cursor.fetchall():
        print(f"  - {name}: {applied}")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
