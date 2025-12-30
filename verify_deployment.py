#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test Railway deployment configuration
"""
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

print("Testing Railway Deployment Configuration")
print("=" * 60)

# Test 1: Check if Django can start
print("\n1️⃣  Testing Django startup...")
try:
    import django
    django.setup()
    print("   ✅ Django initialized successfully")
except Exception as e:
    print(f"   ❌ Django startup failed: {e}")
    sys.exit(1)

# Test 2: Check health endpoint
print("\n2️⃣  Testing health endpoint...")
try:
    from django.test import RequestFactory
    from apps.core.cors_test_views import railway_health_check
    
    factory = RequestFactory()
    request = factory.get('/api/v1/health/')
    response = railway_health_check(request)
    
    if response.status_code == 200:
        print(f"   ✅ Health endpoint returns 200 OK")
        print(f"   📄 Response: {response.content.decode()[:100]}")
    else:
        print(f"   ❌ Health endpoint failed: {response.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"   ❌ Health endpoint test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Check WSGI application
print("\n3️⃣  Testing WSGI application...")
try:
    from config.wsgi import application
    print(f"   ✅ WSGI application loaded: {application}")
except Exception as e:
    print(f"   ❌ WSGI application failed: {e}")
    sys.exit(1)

# Test 4: Check critical settings
print("\n4️⃣  Checking settings...")
try:
    from django.conf import settings
    
    print(f"   📋 DEBUG: {settings.DEBUG}")
    print(f"   📋 ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
    print(f"   📋 DATABASES: {list(settings.DATABASES.keys())}")
    
    if '*' in settings.ALLOWED_HOSTS or '.railway.app' in settings.ALLOWED_HOSTS:
        print("   ✅ ALLOWED_HOSTS configured for Railway")
    else:
        print("   ⚠️  ALLOWED_HOSTS might need Railway domain")
    
except Exception as e:
    print(f"   ❌ Settings check failed: {e}")
    sys.exit(1)

# Test 5: Check requirements
print("\n5️⃣  Checking critical packages...")
packages = {
    'django': 'Django',
    'rest_framework': 'Django REST Framework',
    'gunicorn': 'Gunicorn',
    'psycopg2': 'PostgreSQL Driver'
}

for module, name in packages.items():
    try:
        __import__(module)
        print(f"   ✅ {name} installed")
    except ImportError:
        print(f"   ❌ {name} NOT installed")
        sys.exit(1)

print("\n" + "=" * 60)
print("🎉 ALL TESTS PASSED - READY FOR RAILWAY DEPLOYMENT")
print("=" * 60)
print("\n📋 Next steps:")
print("   1. Commit changes: git add . && git commit -m 'Rebuild Railway deployment'")
print("   2. Push to GitHub: git push origin main")
print("   3. Railway will auto-deploy")
print("   4. Monitor logs in Railway dashboard")
print("")
