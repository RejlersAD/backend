#!/usr/bin/env python3
"""
Railway Environment Validation Script
Checks all required environment variables for deployment
"""
import os
import sys

def validate_environment():
    """Validate Railway environment variables"""
    print("=" * 70)
    print("🔍 Railway Environment Validation")
    print("=" * 70)
    
    errors = []
    warnings = []
    
    # Critical variables (deployment will fail without these)
    critical_vars = {
        'DATABASE_URL': 'PostgreSQL database connection string',
        'SECRET_KEY': 'Django secret key for cryptographic signing',
    }
    
    # Important variables (deployment works but with degraded functionality)
    important_vars = {
        'REDIS_URL': 'Redis connection for cache and Celery (falls back to in-memory)',
        'AWS_ACCESS_KEY_ID': 'AWS S3 access for file storage',
        'AWS_SECRET_ACCESS_KEY': 'AWS S3 secret key',
        'AWS_STORAGE_BUCKET_NAME': 'S3 bucket name for file storage',
    }
    
    # Optional variables
    optional_vars = {
        'ENVIRONMENT': 'Environment name (defaults to "local")',
        'DEBUG': 'Debug mode (should be False in production)',
        'ALLOWED_HOSTS': 'Comma-separated list of allowed hosts',
        'FRONTEND_URL': 'Frontend URL for CORS',
        'BACKEND_URL': 'Backend URL for CORS',
    }
    
    print("\n📋 CRITICAL VARIABLES (deployment fails without these):")
    print("-" * 70)
    for var, description in critical_vars.items():
        value = os.getenv(var)
        if not value:
            errors.append(f"❌ {var} is NOT SET")
            print(f"❌ {var}")
            print(f"   Description: {description}")
            print(f"   Status: NOT SET - DEPLOYMENT WILL FAIL")
        elif var == 'DATABASE_URL':
            # Validate DATABASE_URL format
            if value.startswith('postgresql://') or value.startswith('postgres://'):
                # Hide password in output
                safe_url = value.split('@')[0].split(':')[:-1]
                safe_url = ':'.join(safe_url) + ':***@' + value.split('@')[1] if '@' in value else '***'
                print(f"✅ {var}")
                print(f"   Value: {safe_url}")
            else:
                errors.append(f"❌ {var} format is invalid (must start with postgresql://)")
                print(f"❌ {var}")
                print(f"   Value: {value[:50]}...")
                print(f"   Status: INVALID FORMAT")
        elif var == 'SECRET_KEY':
            if len(value) < 50:
                warnings.append(f"⚠️  {var} is too short (should be 50+ characters)")
                print(f"⚠️  {var}")
                print(f"   Length: {len(value)} characters (should be 50+)")
            elif value == 'django-insecure-change-this-in-production':
                errors.append(f"❌ {var} is using default insecure value")
                print(f"❌ {var}")
                print(f"   Status: USING DEFAULT VALUE - INSECURE")
            else:
                print(f"✅ {var}")
                print(f"   Length: {len(value)} characters")
        else:
            print(f"✅ {var}")
            print(f"   Value: {value[:30]}...")
        print()
    
    print("\n📋 IMPORTANT VARIABLES (optional but recommended):")
    print("-" * 70)
    for var, description in important_vars.items():
        value = os.getenv(var)
        if not value:
            warnings.append(f"⚠️  {var} is not set")
            print(f"⚠️  {var}")
            print(f"   Description: {description}")
            print(f"   Status: NOT SET - will use fallback")
        elif var == 'REDIS_URL':
            # Validate REDIS_URL format
            if value.startswith('redis://'):
                # Check if URL is complete
                if '@:' in value or value.endswith(':'):
                    errors.append(f"❌ {var} format is incomplete (missing host/port)")
                    print(f"❌ {var}")
                    print(f"   Value: {value}")
                    print(f"   Status: INCOMPLETE - missing host/port after @")
                else:
                    safe_url = value.split('@')[0].split(':')[:-1]
                    safe_url = ':'.join(safe_url) + ':***@' + value.split('@')[1] if '@' in value else '***'
                    print(f"✅ {var}")
                    print(f"   Value: {safe_url}")
            else:
                warnings.append(f"⚠️  {var} format may be invalid")
                print(f"⚠️  {var}")
                print(f"   Value: {value[:50]}...")
                print(f"   Status: May be invalid (should start with redis://)")
        else:
            print(f"✅ {var}")
            print(f"   Value: {value[:30]}..." if len(value) > 30 else f"   Value: {value}")
        print()
    
    print("\n📋 OPTIONAL VARIABLES:")
    print("-" * 70)
    for var, description in optional_vars.items():
        value = os.getenv(var)
        if value:
            print(f"✅ {var}")
            print(f"   Value: {value}")
        else:
            print(f"ℹ️  {var}")
            print(f"   Status: Not set (will use default)")
            print(f"   Description: {description}")
        print()
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 VALIDATION SUMMARY")
    print("=" * 70)
    
    if errors:
        print(f"\n❌ {len(errors)} CRITICAL ERROR(S) FOUND:")
        for error in errors:
            print(f"   {error}")
        print("\n🚨 DEPLOYMENT WILL FAIL - Fix these errors before deploying")
    
    if warnings:
        print(f"\n⚠️  {len(warnings)} WARNING(S):")
        for warning in warnings:
            print(f"   {warning}")
        print("\n⚡ Deployment will work but may have reduced functionality")
    
    if not errors and not warnings:
        print("\n✅ ALL CHECKS PASSED - Environment is properly configured")
    
    print("=" * 70)
    
    # Return appropriate exit code
    if errors:
        sys.exit(1)  # Critical errors - fail
    else:
        sys.exit(0)  # Warnings are OK - continue


if __name__ == '__main__':
    try:
        validate_environment()
    except Exception as e:
        print(f"\n❌ Validation script failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
