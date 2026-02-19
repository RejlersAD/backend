"""
Direct Railway PostgreSQL Connection Test with Extended Timeout
"""
import psycopg2
import socket

def test_port_connectivity():
    """Test if port 38534 is reachable"""
    print("🔍 Testing network connectivity to Railway...")
    print("=" * 60)
    
    host = "shinkansen.proxy.rlwy.net"
    port = 38534
    
    try:
        # Create socket with extended timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)  # 30 second timeout
        
        print(f"📡 Attempting to connect to {host}:{port}...")
        print(f"   (Timeout: 30 seconds)")
        
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:
            print(f"✅ Port {port} is REACHABLE")
            return True
        else:
            print(f"❌ Port {port} is NOT REACHABLE (Error code: {result})")
            print(f"\n💡 Possible causes:")
            print(f"   • Corporate firewall blocking port {port}")
            print(f"   • Network proxy/VPN restrictions")
            print(f"   • Railway database not accepting external connections")
            print(f"   • Need to whitelist your IP in Railway dashboard")
            return False
            
    except socket.timeout:
        print(f"❌ Connection TIMEOUT after 30 seconds")
        return False
    except socket.gaierror as e:
        print(f"❌ DNS resolution failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Network error: {e}")
        return False

def test_database_connection():
    """Test actual PostgreSQL connection"""
    print("\n🔍 Testing PostgreSQL database connection...")
    print("=" * 60)
    
    DATABASE_URL = "postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway"
    
    try:
        print("📡 Connecting to Railway PostgreSQL database...")
        print("   (Timeout: 30 seconds)")
        
        # Attempt connection with extended timeout
        conn = psycopg2.connect(
            DATABASE_URL,
            connect_timeout=30,
            options="-c statement_timeout=30000"
        )
        
        print("✅ Successfully connected to Railway PostgreSQL!")
        
        # Test query
        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        
        print(f"\n📊 Database Information:")
        print(f"   Version: {version[:80]}...")
        
        # Get table count
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        table_count = cursor.fetchone()[0]
        print(f"   Tables: {table_count}")
        
        # Get database size
        cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        db_size = cursor.fetchone()[0]
        print(f"   Size: {db_size}")
        
        cursor.close()
        conn.close()
        
        print("\n✅ Connection test PASSED")
        return True
        
    except psycopg2.OperationalError as e:
        print(f"❌ Connection FAILED: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    print("\n🚀 Railway PostgreSQL Connectivity Test")
    print("=" * 60)
    
    # Test 1: Port connectivity
    port_ok = test_port_connectivity()
    
    if not port_ok:
        print("\n⚠️  Cannot reach Railway server - network/firewall issue")
        print("\n💡 Solutions:")
        print("   1. Check if you're behind a corporate firewall (most likely)")
        print("   2. Try connecting from a different network")
        print("   3. Use VPN if Railway requires it")
        print("   4. Contact IT to allow port 38534 outbound")
        print("   5. Use local PostgreSQL for development")
        exit(1)
    
    # Test 2: Database connection
    db_ok = test_database_connection()
    
    if db_ok:
        print("\n✅ Railway database is fully accessible!")
        exit(0)
    else:
        print("\n❌ Railway database connection failed")
        exit(1)
