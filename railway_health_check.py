#!/usr/bin/env python
"""
Railway Health Check Script
Tests critical components before deployment
"""
import os
import sys
import django

def check_environment():
    """Check required environment variables"""
    required_vars = ['DATABASE_URL', 'SECRET_KEY']
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        return False
    
    print("✅ Environment variables OK")
    return True

def check_django():
    """Check Django can be imported and configured"""
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        django.setup()
        print("✅ Django configuration OK")
        return True
    except Exception as e:
        print(f"❌ Django configuration failed: {e}")
        return False

def check_database():
    """Check database connectivity"""
    try:
        from django.db import connection
        connection.ensure_connection()
        print("✅ Database connection OK")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

if __name__ == '__main__':
    print("=" * 50)
    print("Railway Health Check")
    print("=" * 50)
    
    checks = [
        check_environment(),
        check_django(),
        check_database(),
    ]
    
    if all(checks):
        print("\n✅ All checks passed!")
        sys.exit(0)
    else:
        print("\n❌ Some checks failed")
        sys.exit(1)
