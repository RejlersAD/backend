#!/usr/bin/env python3
"""
============================================================================
RAILWAY DATABASE CONFIGURATION VERIFIER
============================================================================
Purpose: Verify database connectivity and environment setup in Railway
Usage: Run in Railway Shell or locally with Railway environment variables
Checks: Database connection, migrations status, environment variables
============================================================================
"""

import os
import sys
from decouple import config

def verify_environment():
    """Verify Railway environment variables"""
    print("=" * 80)
    print("RAILWAY ENVIRONMENT VERIFICATION")
    print("=" * 80)
    
    # Required variables
    required_vars = [
        'DATABASE_URL',
        'DJANGO_SETTINGS_MODULE',
        'DJANGO_SECRET_KEY',
        'ENVIRONMENT',
    ]
    
    optional_vars = [
        'PROD_DATABASE_URL',
        'TEST_DATABASE_URL',
        'AWS_ACCESS_KEY_ID',
        'RAILWAY_ENVIRONMENT_NAME',
        'RAILWAY_GIT_COMMIT_SHA',
    ]
    
    print("\n📋 Required Variables:")
    missing = []
    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Mask sensitive data
            if 'SECRET' in var or 'PASSWORD' in var or 'KEY' in var:
                display_value = value[:10] + "..." if len(value) > 10 else "***"
            elif 'DATABASE_URL' in var:
                display_value = value.split('@')[1] if '@' in value else "***"
            else:
                display_value = value
            print(f"  ✅ {var}: {display_value}")
        else:
            print(f"  ❌ {var}: NOT SET")
            missing.append(var)
    
    print("\n📋 Optional Variables:")
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            if 'SECRET' in var or 'PASSWORD' in var or 'KEY' in var:
                display_value = "***"
            elif 'DATABASE_URL' in var:
                display_value = value.split('@')[1] if '@' in value else "***"
            else:
                display_value = value
            print(f"  ✅ {var}: {display_value}")
        else:
            print(f"  ⚠️  {var}: Not set (optional)")
    
    if missing:
        print(f"\n❌ Missing {len(missing)} required variables!")
        print("   Set these in Railway Dashboard → Variables")
        return False
    
    print("\n✅ All required environment variables are set!")
    return True

def verify_database_connection():
    """Test database connectivity"""
    print("\n" + "=" * 80)
    print("DATABASE CONNECTION TEST")
    print("=" * 80)
    
    try:
        import psycopg2
        from urllib.parse import urlparse
        
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            print("❌ DATABASE_URL not found")
            return False
        
        # Parse database URL
        parsed = urlparse(db_url)
        
        print(f"\n📊 Database Details:")
        print(f"  Host: {parsed.hostname}:{parsed.port}")
        print(f"  Database: {parsed.path[1:]}")
        print(f"  User: {parsed.username}")
        
        # Test connection
        print("\n🔌 Testing connection...")
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # Get database info
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"  ✅ Connected to PostgreSQL")
        print(f"  Version: {version.split(',')[0]}")
        
        # Count tables
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        table_count = cursor.fetchone()[0]
        print(f"  Tables: {table_count}")
        
        cursor.close()
        conn.close()
        
        print("\n✅ Database connection successful!")
        return True
        
    except ImportError:
        print("❌ psycopg2 not installed")
        print("   Run: pip install psycopg2-binary")
        return False
    except Exception as e:
        print(f"❌ Database connection failed: {str(e)}")
        return False

def verify_django_setup():
    """Verify Django configuration"""
    print("\n" + "=" * 80)
    print("DJANGO CONFIGURATION CHECK")
    print("=" * 80)
    
    try:
        import django
        from django.conf import settings
        
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 
                             os.getenv('DJANGO_SETTINGS_MODULE', 'config.settings.production'))
        django.setup()
        
        print(f"\n✅ Django {django.get_version()} initialized")
        print(f"  Settings: {os.getenv('DJANGO_SETTINGS_MODULE')}")
        print(f"  Debug: {settings.DEBUG}")
        print(f"  Database: {settings.DATABASES['default']['NAME']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Django setup failed: {str(e)}")
        return False

def check_migrations():
    """Check migrations status"""
    print("\n" + "=" * 80)
    print("MIGRATIONS STATUS")
    print("=" * 80)
    
    try:
        from django.core.management import execute_from_command_line
        print("\nRunning: python manage.py showmigrations --plan\n")
        execute_from_command_line(['manage.py', 'showmigrations'])
        return True
    except Exception as e:
        print(f"❌ Could not check migrations: {str(e)}")
        return False

def main():
    """Run all verification checks"""
    print("\n🚀 Starting Railway Environment Verification...\n")
    
    checks = [
        ("Environment Variables", verify_environment),
        ("Database Connection", verify_database_connection),
        ("Django Setup", verify_django_setup),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} check crashed: {str(e)}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {status}: {name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n🎉 All checks passed! Railway environment is properly configured.")
        return 0
    else:
        print("\n⚠️  Some checks failed. Review the errors above.")
        print("   Refer to RAILWAY_ENV_CONFIG.txt for setup instructions.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
