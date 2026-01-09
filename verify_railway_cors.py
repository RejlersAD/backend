#!/usr/bin/env python
"""
Railway CORS Verification Script
Tests if CORS is properly configured on the Railway backend
"""

import requests
import sys

BACKEND_URL = "https://aiflowbackend-production.up.railway.app"
FRONTEND_ORIGIN = "https://www.radai.ae"

print("\n" + "="*70)
print("🔍 Testing CORS Configuration on Railway")
print("="*70)

print(f"\n📡 Backend URL: {BACKEND_URL}")
print(f"🌐 Frontend Origin: {FRONTEND_ORIGIN}")

# Test 1: OPTIONS preflight request
print("\n" + "-"*70)
print("Test 1: OPTIONS Preflight Request")
print("-"*70)

try:
    response = requests.options(
        f"{BACKEND_URL}/api/v1/auth/login/",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,Authorization"
        },
        timeout=10
    )
    
    print(f"\n✅ Status Code: {response.status_code}")
    
    cors_headers = {
        key: value 
        for key, value in response.headers.items() 
        if 'access-control' in key.lower()
    }
    
    if cors_headers:
        print("\n📋 CORS Headers Found:")
        for key, value in cors_headers.items():
            print(f"   {key}: {value}")
        
        # Check critical headers
        if 'access-control-allow-origin' in response.headers:
            origin = response.headers['access-control-allow-origin']
            if origin == FRONTEND_ORIGIN or origin == '*':
                print(f"\n✅ CORS Origin: {origin}")
            else:
                print(f"\n❌ Wrong CORS Origin: {origin} (expected: {FRONTEND_ORIGIN})")
        else:
            print("\n❌ Missing 'Access-Control-Allow-Origin' header")
            
        if 'access-control-allow-credentials' in response.headers:
            creds = response.headers['access-control-allow-credentials']
            print(f"✅ Allow Credentials: {creds}")
        
        if 'access-control-allow-methods' in response.headers:
            methods = response.headers['access-control-allow-methods']
            print(f"✅ Allowed Methods: {methods}")
            
    else:
        print("\n❌ No CORS headers found in response!")
        print("\n🔥 PROBLEM: Railway backend is not sending CORS headers")
        print("\n💡 SOLUTION:")
        print("   1. Go to Railway Dashboard")
        print("   2. Select 'aiflowbackend-production' project")
        print("   3. Click 'Variables' tab")
        print("   4. Add these variables:")
        print(f"      CORS_ALLOW_ALL_ORIGINS=False")
        print(f"      CORS_ALLOWED_ORIGINS={FRONTEND_ORIGIN},https://radai.ae")
        print("   5. Railway will auto-redeploy (wait 2-3 minutes)")
        print("   6. Run this script again")
        
except requests.exceptions.RequestException as e:
    print(f"\n❌ Request Failed: {e}")
    print("\n🔥 PROBLEM: Cannot reach Railway backend")
    print("\n💡 SOLUTION:")
    print("   1. Check if Railway deployment is running")
    print("   2. Check Railway logs for errors")
    print("   3. Verify DATABASE_URL is set")

# Test 2: Health check
print("\n" + "-"*70)
print("Test 2: Backend Health Check")
print("-"*70)

try:
    response = requests.get(f"{BACKEND_URL}/api/v1/health/", timeout=10)
    print(f"\n✅ Status Code: {response.status_code}")
    print(f"✅ Response: {response.json()}")
except requests.exceptions.RequestException as e:
    print(f"\n❌ Health Check Failed: {e}")

# Summary
print("\n" + "="*70)
print("📊 Summary")
print("="*70)

print("\n📝 Next Steps:")
print("   1. If CORS headers are missing → Set environment variables on Railway")
print("   2. If status is 500 → Check Railway logs for Django errors")
print("   3. If timeout → Check if Railway service is running")
print("   4. After fixing → Wait 2-3 minutes and run this script again")
print("\n")

print("📚 Documentation: See RAILWAY_CORS_FIX.md for detailed instructions")
print("\n")
