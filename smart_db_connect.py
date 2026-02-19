"""
Smart Database Connection Manager
Tests Railway database connectivity and falls back to local if unavailable
"""
import os
import sys
import socket
import psycopg2
from urllib.parse import urlparse

def test_database_connection(db_url, timeout=5):
    """Test if database is accessible"""
    try:
        # Parse database URL
        parsed = urlparse(db_url)
        host = parsed.hostname
        port = parsed.port or 5432
        
        # Test socket connection first
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result != 0:
            return False, f"Cannot reach {host}:{port}"
        
        # Test actual database connection
        conn = psycopg2.connect(db_url, connect_timeout=timeout)
        cursor = conn.cursor()
        cursor.execute('SELECT 1')
        conn.close()
        return True, "Connection successful"
        
    except Exception as e:
        return False, str(e)

def get_smart_database_url():
    """Return best available database URL"""
    
    # Railway Production Database
    railway_url = "postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway"
    
    # Local Development Database
    local_url = "postgresql://aiflow_user:aiflow_local_pass_123@postgres_local:5432/aiflow_dev"
    
    print("🔍 Smart Database Connection Manager")
    print("=" * 60)
    
    # Test Railway database
    print("\n1️⃣  Testing Railway Production Database...")
    print(f"   Host: shinkansen.proxy.rlwy.net:38534")
    railway_ok, railway_msg = test_database_connection(railway_url, timeout=8)
    
    if railway_ok:
        print(f"   ✅ {railway_msg}")
        print(f"\n🎯 Using: RAILWAY PRODUCTION DATABASE")
        print(f"   Database: railway")
        print(f"   Users: 200+ (production data)")
        return railway_url, "railway"
    else:
        print(f"   ❌ {railway_msg}")
    
    # Test Local database
    print("\n2️⃣  Testing Local Development Database...")
    print(f"   Host: postgres_local:5432")
    local_ok, local_msg = test_database_connection(local_url, timeout=5)
    
    if local_ok:
        print(f"   ✅ {local_msg}")
        print(f"\n🎯 Using: LOCAL DEVELOPMENT DATABASE")
        print(f"   Database: aiflow_dev")
        print(f"   Users: ~29 (development data)")
        return local_url, "local"
    else:
        print(f"   ❌ {local_msg}")
    
    print("\n⚠️  No database available!")
    return None, None

if __name__ == "__main__":
    db_url, db_type = get_smart_database_url()
    
    if db_url:
        print("\n" + "=" * 60)
        print("📝 To use this database, update .env.local with:")
        print(f"DATABASE_URL={db_url}")
        sys.exit(0)
    else:
        print("\n❌ Cannot connect to any database")
        sys.exit(1)
