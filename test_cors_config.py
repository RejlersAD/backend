#!/usr/bin/env python
"""
CORS Configuration Tester for Railway Deployment
Tests if CORS is configured correctly
"""

import os
import sys

def check_cors_config():
    """Check current CORS configuration"""
    print("\n" + "="*70)
    print("🔍 CORS CONFIGURATION CHECKER")
    print("="*70 + "\n")
    
    # Set Django settings
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    
    try:
        import django
        django.setup()
        
        from django.conf import settings
        
        print("✅ Django settings loaded successfully!\n")
        
        # Check CORS settings
        print("="*70)
        print("CORS CONFIGURATION:")
        print("="*70)
        
        cors_allow_all = getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False)
        print(f"CORS_ALLOW_ALL_ORIGINS: {cors_allow_all}")
        
        if cors_allow_all:
            print("⚠️  WARNING: All origins are allowed (less secure)")
            print("⚠️  CORS_ALLOW_CREDENTIALS will be set to False automatically")
        else:
            cors_origins = getattr(settings, 'CORS_ALLOWED_ORIGINS', [])
            print(f"\n✓ Specific origins allowed ({len(cors_origins)}):")
            for origin in cors_origins:
                print(f"  - {origin}")
        
        print(f"\nCORS_ALLOW_CREDENTIALS: {getattr(settings, 'CORS_ALLOW_CREDENTIALS', False)}")
        print(f"CORS_ALLOW_METHODS: {getattr(settings, 'CORS_ALLOW_METHODS', [])}")
        print(f"CORS_PREFLIGHT_MAX_AGE: {getattr(settings, 'CORS_PREFLIGHT_MAX_AGE', 0)}s")
        
        # Check middleware
        print("\n" + "="*70)
        print("MIDDLEWARE CONFIGURATION:")
        print("="*70)
        
        middleware = getattr(settings, 'MIDDLEWARE', [])
        cors_middleware = 'corsheaders.middleware.CorsMiddleware'
        common_middleware = 'django.middleware.common.CommonMiddleware'
        
        if cors_middleware in middleware:
            cors_index = middleware.index(cors_middleware)
            print(f"✓ CorsMiddleware found at position {cors_index}")
            
            if common_middleware in middleware:
                common_index = middleware.index(common_middleware)
                if cors_index < common_index:
                    print(f"✓ CorsMiddleware is BEFORE CommonMiddleware (position {common_index}) ✓")
                else:
                    print(f"❌ ERROR: CorsMiddleware is AFTER CommonMiddleware!")
                    print("   This will cause CORS to fail!")
            else:
                print("⚠️  CommonMiddleware not found")
        else:
            print(f"❌ ERROR: {cors_middleware} not found in MIDDLEWARE!")
            print("   CORS will not work!")
        
        # Check INSTALLED_APPS
        print("\n" + "="*70)
        print("INSTALLED APPS:")
        print("="*70)
        
        installed_apps = getattr(settings, 'INSTALLED_APPS', [])
        if 'corsheaders' in installed_apps:
            print("✓ 'corsheaders' is installed")
        else:
            print("❌ ERROR: 'corsheaders' not in INSTALLED_APPS!")
        
        # Check URLs
        print("\n" + "="*70)
        print("URL CONFIGURATION:")
        print("="*70)
        
        frontend_url = getattr(settings, 'FRONTEND_URL', 'NOT SET')
        backend_url = getattr(settings, 'BACKEND_URL', 'NOT SET')
        print(f"FRONTEND_URL: {frontend_url}")
        print(f"BACKEND_URL: {backend_url}")
        
        # Check CSRF
        print("\n" + "="*70)
        print("CSRF CONFIGURATION:")
        print("="*70)
        
        csrf_trusted = getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])
        print(f"CSRF_TRUSTED_ORIGINS ({len(csrf_trusted)}):")
        for origin in csrf_trusted:
            print(f"  - {origin}")
        
        # Summary
        print("\n" + "="*70)
        print("SUMMARY:")
        print("="*70)
        
        issues = []
        
        if cors_middleware not in middleware:
            issues.append("CorsMiddleware not in MIDDLEWARE")
        elif cors_index >= common_index:
            issues.append("CorsMiddleware is after CommonMiddleware")
        
        if 'corsheaders' not in installed_apps:
            issues.append("'corsheaders' not in INSTALLED_APPS")
        
        if cors_allow_all and getattr(settings, 'CORS_ALLOW_CREDENTIALS', False):
            issues.append("CORS_ALLOW_ALL_ORIGINS=True conflicts with CORS_ALLOW_CREDENTIALS=True")
        
        if not cors_allow_all and not cors_origins:
            issues.append("No CORS origins configured")
        
        if issues:
            print("\n❌ ISSUES FOUND:")
            for i, issue in enumerate(issues, 1):
                print(f"{i}. {issue}")
            print("\n⚠️  CORS may not work correctly!")
            return False
        else:
            print("\n✅ All checks passed! CORS should work correctly.")
            
            print("\n📋 NEXT STEPS:")
            print("1. Push code to Railway")
            print("2. Check Railway logs for CORS configuration output")
            print("3. Test with: curl -I https://your-app.railway.app/health/")
            return True
            
    except ImportError as e:
        print(f"❌ ERROR: Failed to import Django: {e}")
        print("\nMake sure you're in the backend directory and Django is installed.")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = check_cors_config()
    sys.exit(0 if success else 1)
