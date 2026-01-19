#!/usr/bin/env python
"""
Check user count in Railway Production PostgreSQL Database
"""
import os
import sys
import psycopg2
from urllib.parse import urlparse

# Railway PostgreSQL connection string
DATABASE_URL = "postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@postgres.railway.internal:5432/railway"

# Parse the DATABASE_URL for external connection (if running locally)
# Railway internal URLs won't work from local machine, so we'll use environment variable if available
connection_url = os.getenv('DATABASE_URL', DATABASE_URL)

print("=" * 70)
print("CONNECTING TO RAILWAY POSTGRESQL DATABASE")
print("=" * 70)
print(f"\nConnection URL: {connection_url[:50]}...")

try:
    # Parse the URL
    result = urlparse(connection_url)
    username = result.username
    password = result.password
    database = result.path[1:]
    hostname = result.hostname
    port = result.port
    
    print(f"Host: {hostname}")
    print(f"Database: {database}")
    print(f"User: {username}")
    print("\n" + "=" * 70)
    print("CONNECTING...")
    print("=" * 70)
    
    # Connect to PostgreSQL
    conn = psycopg2.connect(
        dbname=database,
        user=username,
        password=password,
        host=hostname,
        port=port,
        connect_timeout=10
    )
    
    cursor = conn.cursor()
    
    print("✓ Connected successfully!\n")
    print("=" * 70)
    print("CHECKING DATABASE TABLES")
    print("=" * 70)
    
    # First, check what tables exist
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    tables = cursor.fetchall()
    
    print(f"\n✓ Found {len(tables)} tables in Railway database:")
    user_related_tables = []
    for table in tables:
        table_name = table[0]
        if 'user' in table_name.lower() or 'auth' in table_name.lower() or 'rbac' in table_name.lower():
            user_related_tables.append(table_name)
            print(f"  - {table_name} ⭐")
        # else:
        #     print(f"  - {table_name}")
    
    if not user_related_tables:
        print("\n⚠️ No user-related tables found! Database might be empty or not migrated.")
        cursor.close()
        conn.close()
        sys.exit(0)
    
    print("\n" + "=" * 70)
    print("QUERYING USER DATA FROM RAILWAY DATABASE")
    print("=" * 70)
    
    # Try to find the correct user table
    user_table = None
    for table in user_related_tables:
        if 'user' in table.lower() and 'profile' not in table.lower():
            user_table = table
            break
    
    if not user_table:
        print("\n⚠️ Could not find main user table")
        user_table = user_related_tables[0] if user_related_tables else None
    
    if user_table:
        cursor.execute(f"SELECT COUNT(*) FROM {user_table};")
        total_users = cursor.fetchone()[0]
        print(f"\n✓ Total records in {user_table}: {total_users}")
    if user_table:
        cursor.execute(f"SELECT COUNT(*) FROM {user_table};")
        total_users = cursor.fetchone()[0]
        print(f"\n✓ Total records in {user_table}: {total_users}")
    
    # Try to find UserProfile table
    profile_table = None
    for table in user_related_tables:
        if 'profile' in table.lower():
            profile_table = table
            break
    
    if profile_table:
        cursor.execute(f"SELECT COUNT(*) FROM {profile_table} WHERE is_deleted = false;")
        active_profiles = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT COUNT(*) FROM {profile_table} WHERE is_deleted = true;")
        deleted_profiles = cursor.fetchone()[0]
        
        total_profiles = active_profiles + deleted_profiles
        
        print(f"✓ Total UserProfiles in {profile_table}: {total_profiles}")
        print(f"  - Active: {active_profiles}")
        print(f"  - Deleted: {deleted_profiles}")
        
        # Get detailed user information
        print("\n" + "=" * 70)
        print("ACTIVE USER DETAILS FROM RAILWAY:")
        print("=" * 70)
        
        # Get column names first
        cursor.execute(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = '{profile_table}'
            ORDER BY ordinal_position;
        """)
        columns = [row[0] for row in cursor.fetchall()]
        print(f"\nColumns in {profile_table}: {', '.join(columns)}")
        
        # Query users
        cursor.execute(f"SELECT * FROM {profile_table} WHERE is_deleted = false LIMIT 20;")
        users = cursor.fetchall()
        
        if users:
            print(f"\nFound {len(users)} active users:")
            for idx, user in enumerate(users, 1):
                print(f"\n{idx}. User Record:")
                for col_idx, col_name in enumerate(columns):
                    if col_idx < len(user):
                        value = user[col_idx]
                        # Truncate long values
                        if isinstance(value, str) and len(value) > 50:
                            value = value[:50] + "..."
                        print(f"   {col_name}: {value}")
        else:
            print("\n⚠ No active users found in Railway database!")
        
        print("\n" + "=" * 70)
        print(f"SUMMARY: Found {active_profiles} active users in Railway PostgreSQL")
        print("=" * 70)
    else:
        print("\n⚠️ Could not find UserProfile table to get detailed info")
        print(f"Available tables: {', '.join(user_related_tables)}")
    
    cursor.close()
    conn.close()
    
except psycopg2.OperationalError as e:
    print(f"\n❌ Connection Error: {e}")
    print("\n💡 NOTE: 'postgres.railway.internal' is only accessible from within Railway.")
    print("   To connect from your local machine, you need the PUBLIC connection string.")
    print("\n   Run this command to get the public URL:")
    print("   railway variables --service postgres")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
