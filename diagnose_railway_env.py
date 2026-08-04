"""
Quick Production Diagnostic Script
Run this to check if your environment variables are set correctly
"""
import os
import sys
from urllib.parse import urlparse

print("=" * 80)
print("🔍 RAILWAY PRODUCTION ENVIRONMENT DIAGNOSTIC")
print("=" * 80)
print()

def check_var(name, required=True, hide_value=False):
    """Check if an environment variable is set"""
    value = os.environ.get(name)
    if value:
        if hide_value:
            # Show only first few characters for security
            display_value = f"{value[:15]}...{value[-10:]}" if len(value) > 25 else "***"
        else:
            display_value = value
        print(f"✅ {name:30} = {display_value}")
        return True
    else:
        if required:
            print(f"❌ {name:30} = NOT SET (REQUIRED)")
        else:
            print(f"⚠️  {name:30} = NOT SET (Optional)")
        return False


print("📋 Critical Environment Variables:")
print("-" * 80)

# Check critical variables
has_database = check_var('DATABASE_URL', required=True, hide_value=True)
has_secret = check_var('SECRET_KEY', required=True, hide_value=True)
check_var('DEBUG', required=True)
check_var('ALLOWED_HOSTS', required=True)

print()
print("📋 Redis Configuration:")
print("-" * 80)
has_redis = check_var('REDIS_URL', required=False, hide_value=True)

print()
print("📋 Optional Variables:")
print("-" * 80)
check_var('PORT', required=False)
check_var('ENVIRONMENT', required=False)
check_var('RAILWAY_ENVIRONMENT', required=False)
check_var('GUNICORN_WORKERS', required=False)
check_var('GUNICORN_TIMEOUT', required=False)

print()
print("=" * 80)
print("🔍 DATABASE CONNECTION ANALYSIS")
print("=" * 80)

if has_database:
    db_url = os.environ.get('DATABASE_URL')
    try:
        parsed = urlparse(db_url)
        print(f"✅ Database URL parsed successfully")
        print(f"   Protocol: {parsed.scheme}")
        print(f"   Host:     {parsed.hostname}")
        print(f"   Port:     {parsed.port}")
        print(f"   Database: {parsed.path.lstrip('/')}")
        print(f"   Username: {parsed.username}")
        print(f"   Password: {'***' if parsed.password else 'NOT SET'}")
        
        # Check if it's a Railway database
        if 'railway' in parsed.hostname:
            print(f"\n✅ Using Railway PostgreSQL")
            if 'proxy.rlwy.net' in parsed.hostname:
                print(f"✅ Using proxy URL (correct for external access)")
            else:
                print(f"⚠️  Not using proxy URL - may only work internally")
        
        # Try to connect
        print(f"\n🔌 Testing database connection...")
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cursor = conn.cursor()
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            print(f"✅ DATABASE CONNECTION SUCCESSFUL!")
            print(f"   PostgreSQL version: {version[:50]}")
            
            # Check if tables exist
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            table_count = cursor.fetchone()[0]
            print(f"   Tables in database: {table_count}")
            
            if table_count == 0:
                print(f"\n⚠️  WARNING: Database has NO TABLES!")
                print(f"   This means migrations haven't run yet.")
                print(f"   Django will try to run migrations on startup.")
            else:
                print(f"✅ Database has tables - looks good!")
            
            conn.close()
            
        except ImportError:
            print(f"⚠️  psycopg2 not installed - cannot test connection")
        except Exception as e:
            print(f"❌ DATABASE CONNECTION FAILED!")
            print(f"   Error: {str(e)}")
            print(f"\n🔍 Troubleshooting:")
            print(f"   1. Check if the database service is running in Railway")
            print(f"   2. Verify the credentials are correct")
            print(f"   3. Make sure you're using the PUBLIC connection string")
            print(f"   4. Try connecting with psql from command line")
            
    except Exception as e:
        print(f"❌ Failed to parse DATABASE_URL: {str(e)}")
else:
    print(f"❌ DATABASE_URL not set - application will NOT start!")
    print(f"\n🔧 FIX:")
    print(f"   1. Go to Railway Dashboard")
    print(f"   2. Select your PostgreSQL service")
    print(f"   3. Go to Connect tab")
    print(f"   4. Copy the 'Public Connection String'")
    print(f"   5. Go to Backend service → Variables tab")
    print(f"   6. Add variable: DATABASE_URL = (paste connection string)")

print()
print("=" * 80)
print("🔍 REDIS CONNECTION ANALYSIS")
print("=" * 80)

if has_redis:
    redis_url = os.environ.get('REDIS_URL')
    try:
        parsed = urlparse(redis_url)
        print(f"✅ Redis URL parsed successfully")
        print(f"   Protocol: {parsed.scheme}")
        print(f"   Host:     {parsed.hostname}")
        print(f"   Port:     {parsed.port}")
        print(f"   Password: {'***' if parsed.password else 'NOT SET'}")
        
        if 'railway.internal' in parsed.hostname:
            print(f"✅ Using internal Railway hostname (correct for backend-to-redis)")
        elif 'proxy.rlwy.net' in parsed.hostname:
            print(f"⚠️  Using proxy URL - consider using .railway.internal for better performance")
        
        # Try to connect
        print(f"\n🔌 Testing Redis connection...")
        try:
            import redis
            r = redis.from_url(redis_url)
            r.ping()
            print(f"✅ REDIS CONNECTION SUCCESSFUL!")
            info = r.info('server')
            print(f"   Redis version: {info.get('redis_version', 'unknown')}")
        except ImportError:
            print(f"⚠️  redis package not installed - cannot test connection")
        except Exception as e:
            print(f"❌ REDIS CONNECTION FAILED!")
            print(f"   Error: {str(e)}")
            print(f"\n⚠️  Redis is optional - Django will fall back to in-memory cache")
            
    except Exception as e:
        print(f"❌ Failed to parse REDIS_URL: {str(e)}")
else:
    print(f"⚠️  REDIS_URL not set")
    print(f"   Django will use in-memory cache (works but not ideal)")
    print(f"   Celery tasks will run synchronously (no async processing)")
    print(f"\n💡 To enable Redis:")
    print(f"   1. Add Redis service in Railway")
    print(f"   2. Copy the connection string")
    print(f"   3. Set REDIS_URL in backend service variables")

print()
print("=" * 80)
print("📊 SUMMARY")
print("=" * 80)

issues_found = []
if not has_database:
    issues_found.append("❌ DATABASE_URL not set - CRITICAL")
if not has_secret:
    issues_found.append("❌ SECRET_KEY not set - CRITICAL")

if issues_found:
    print(f"\n🚨 {len(issues_found)} CRITICAL ISSUE(S) FOUND:")
    for issue in issues_found:
        print(f"   {issue}")
    print(f"\n❌ Application will NOT start until these are fixed!")
    print(f"\n🔧 Next steps:")
    print(f"   1. Go to Railway Dashboard → Backend Service → Variables")
    print(f"   2. Set the missing environment variables")
    print(f"   3. Redeploy the service")
    sys.exit(1)
else:
    print(f"\n✅ All critical environment variables are set!")
    print(f"\n🚀 Application should be able to start.")
    print(f"\n💡 If deployment is still failing:")
    print(f"   1. Check Railway deployment logs for specific errors")
    print(f"   2. Verify database migrations have run")
    print(f"   3. Test health endpoint: curl https://your-backend.up.railway.app/api/v1/health/")

print()
print("=" * 80)
