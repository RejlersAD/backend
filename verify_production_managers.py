"""
Verify RadAI Managers in Production Database
Using soft-coded API calls to check production state
"""
import requests
import json
import sys

# SOFT-CODED: Production configuration
PRODUCTION_BACKEND = "https://aiflowbackend-production.up.railway.app"
PRODUCTION_FRONTEND = "https://www.radai.ae"

# Managers we expect to find
EXPECTED_MANAGERS = [
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae', 
    'aleksi.murtomaki@rejlers.ae'
]

def check_production_managers():
    """Check if managers exist in production by calling API"""
    
    print("=" * 80)
    print("🔍 PRODUCTION ENVIRONMENT VERIFICATION")
    print("=" * 80)
    print(f"Backend: {PRODUCTION_BACKEND}")
    print(f"Frontend: {PRODUCTION_FRONTEND}")
    print()
    
    # Step 1: Get admin login credentials
    print("📋 Step 1: Login to Production")
    print("-" * 80)
    
    email = input("Enter admin email: ").strip()
    if not email:
        print("❌ Email required")
        sys.exit(1)
    
    password = input("Enter admin password: ").strip()
    if not password:
        print("❌ Password required")
        sys.exit(1)
    
    # Step 2: Login
    print("\n🔐 Authenticating...")
    try:
        login_response = requests.post(
            f"{PRODUCTION_BACKEND}/api/v1/users/login/",
            json={"email": email, "password": password},
            timeout=10
        )
        
        if login_response.status_code != 200:
            print(f"❌ Login failed: {login_response.status_code}")
            print(f"Response: {login_response.text}")
            sys.exit(1)
        
        token = login_response.json().get('access')
        if not token:
            print("❌ No access token in response")
            sys.exit(1)
        
        print("✅ Login successful")
        
    except Exception as e:
        print(f"❌ Login error: {e}")
        sys.exit(1)
    
    # Step 3: Get engineers list
    print("\n📊 Step 2: Fetching Engineers List")
    print("-" * 80)
    
    try:
        engineers_response = requests.get(
            f"{PRODUCTION_BACKEND}/api/v1/rbac/users/engineers/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        
        if engineers_response.status_code != 200:
            print(f"❌ Failed to fetch engineers: {engineers_response.status_code}")
            print(f"Response: {engineers_response.text}")
            sys.exit(1)
        
        data = engineers_response.json()
        engineers = data.get('engineers', [])
        total_count = data.get('count', len(engineers))
        
        print(f"✅ Retrieved {total_count} engineers from production")
        
    except Exception as e:
        print(f"❌ API error: {e}")
        sys.exit(1)
    
    # Step 4: Check for RadAI managers
    print("\n🔍 Step 3: Checking for RadAI Managers")
    print("-" * 80)
    
    radai_managers = [
        eng for eng in engineers 
        if eng.get('department') == 'radai'
    ]
    
    found_emails = {mgr['email'] for mgr in radai_managers}
    missing_emails = [email for email in EXPECTED_MANAGERS if email not in found_emails]
    
    print(f"\nRadAI Managers Found: {len(radai_managers)}")
    for mgr in radai_managers:
        status = "✅" if mgr['email'] in EXPECTED_MANAGERS else "ℹ️"
        print(f"  {status} {mgr['name']} ({mgr['email']})")
        print(f"     Job Title: {mgr.get('job_title', 'N/A')}, Department: {mgr.get('department', 'N/A')}")
    
    if missing_emails:
        print(f"\n❌ MISSING MANAGERS ({len(missing_emails)}):")
        for email in missing_emails:
            print(f"  ❌ {email}")
    else:
        print("\n✅ ALL EXPECTED MANAGERS FOUND IN PRODUCTION!")
    
    # Step 5: Show all departments
    print("\n📋 All Departments in Production:")
    print("-" * 80)
    dept_counts = {}
    for eng in engineers:
        dept = eng.get('department', 'N/A')
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
    
    for dept, count in sorted(dept_counts.items()):
        marker = "✅" if dept == 'radai' else "  "
        print(f"{marker} {dept}: {count} engineers")
    
    # Summary
    print("\n" + "=" * 80)
    print("📊 VERIFICATION SUMMARY")
    print("=" * 80)
    print(f"Total Engineers: {total_count}")
    print(f"RadAI Managers Found: {len(radai_managers)}/{len(EXPECTED_MANAGERS)}")
    print(f"Missing Managers: {len(missing_emails)}")
    
    if missing_emails:
        print("\n⚠️  ACTION REQUIRED:")
        print("   Run the create_radai_managers command in production:")
        print("   1. Use PowerShell script: .\\run_create_managers.ps1")
        print("   2. Or call API endpoint: POST /api/v1/rbac/admin/create-radai-managers/")
        return False
    else:
        print("\n🎉 SUCCESS! Production database is configured correctly!")
        return True

if __name__ == '__main__':
    check_production_managers()
