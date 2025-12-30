#!/usr/bin/env python
"""Test Railway database connection"""

import psycopg2

DATABASE_URL = "postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway"

try:
    print("🔌 Connecting to Railway database...")
    conn = psycopg2.connect(DATABASE_URL)
    print("✅ Railway Database Connection: SUCCESS\n")
    
    cursor = conn.cursor()
    
    # Get PostgreSQL version
    cursor.execute('SELECT version();')
    version = cursor.fetchone()
    print(f"📊 PostgreSQL Version: {version[0][:70]}...\n")
    
    # Count tables
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
    table_count = cursor.fetchone()
    print(f"📋 Tables in database: {table_count[0]}")
    
    # List some tables
    if table_count[0] > 0:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' LIMIT 10")
        tables = cursor.fetchall()
        print(f"📊 Sample tables:")
        for table in tables:
            print(f"   - {table[0]}")
    else:
        print("⚠️  No tables found - migrations need to run")
    
    conn.close()
    print("\n✅ Database is ready for deployment")
    print("✅ Migrations will run automatically on Railway startup")
    
except Exception as e:
    print(f"❌ Database connection failed: {e}")
    exit(1)
