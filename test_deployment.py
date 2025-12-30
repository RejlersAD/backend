#!/usr/bin/env python
"""Test simple_health.py functionality"""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

print("Testing simple_health.py...")

try:
    import simple_health
    print("✅ simple_health.py imports successfully")
    
    # Check callable exists
    if callable(simple_health.application):
        print("✅ application callable exists")
    else:
        print("❌ application is not callable")
        exit(1)
    
    # Test health check endpoint
    test_environ = {'PATH_INFO': '/api/v1/health/'}
    responses = []
    
    def mock_start_response(status, headers):
        responses.append((status, headers))
    
    result = simple_health.application(test_environ, mock_start_response)
    
    if responses and responses[0][0] == '200 OK':
        print("✅ Health check returns 200 OK")
    else:
        print(f"❌ Health check failed: {responses}")
        exit(1)
    
    if result and b'healthy' in result[0]:
        print("✅ Health check response contains 'healthy'")
    else:
        print(f"❌ Invalid response: {result}")
        exit(1)
    
    print("\n✅ ALL TESTS PASSED - simple_health.py is ready for deployment!")
    
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
