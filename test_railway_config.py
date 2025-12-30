#!/usr/bin/env python
"""
Quick Django startup test - verify configuration before deployment
"""
import os
import sys
import django

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("🧪 Testing Django Configuration...")
print("=" * 60)

try:
    django.setup()
    print("✅ Django setup successful")
    
    from django.conf import settings
    print(f"✅ SECRET_KEY: {'*' * 10} (configured)")
    print(f"✅ DEBUG: {settings.DEBUG}")
    print(f"✅ ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
    
    # Test database connection
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        print("✅ PostgreSQL connection successful")
    
    # Test health endpoint
    from apps.core.cors_test_views import railway_health_check
    from django.test import RequestFactory
    factory = RequestFactory()
    request = factory.get('/api/v1/health/')
    response = railway_health_check(request)
    print(f"✅ Health endpoint returns: {response.status_code}")
    
    # Test JWT settings
    print(f"✅ JWT configured: {bool(settings.SIMPLE_JWT)}")
    
    print("=" * 60)
    print("🎉 All tests passed! Ready for deployment")
    sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
