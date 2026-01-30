"""
Compare Production vs Local - Test user data endpoints
"""
import requests
import json

print("=" * 100)
print("COMPARING PRODUCTION vs LOCAL ENVIRONMENTS")
print("=" * 100)

# Test credentials
email = "tanzeem.agra@rejlers.ae"
password = "Tanzilla@tanzeem786"

def test_environment(name, base_url):
    print(f"\n{'=' * 100}")
    print(f"Testing {name}: {base_url}")
    print("=" * 100)
    
    try:
        # 1. Test login
        login_url = f"{base_url}/api/v1/auth/login/"
        print(f"\n1. Testing login at: {login_url}")
        
        login_response = requests.post(
            login_url, 
            json={"email": email, "password": password},
            timeout=10
        )
        
        print(f"   Status: {login_response.status_code}")
        
        if login_response.status_code != 200:
            print(f"   ❌ Login failed: {login_response.text}")
            return
        
        tokens = login_response.json()
        print(f"   ✅ Login successful")
        
        # 2. Test /rbac/users/me/
        me_url = f"{base_url}/api/v1/rbac/users/me/"
        print(f"\n2. Testing user data at: {me_url}")
        
        headers = {"Authorization": f"Bearer {tokens['access']}"}
        me_response = requests.get(me_url, headers=headers, timeout=10)
        
        print(f"   Status: {me_response.status_code}")
        
        if me_response.status_code != 200:
            print(f"   ❌ Failed: {me_response.text}")
            return
        
        user_data = me_response.json()
        print(f"   ✅ User data retrieved")
        
        # 3. Display key fields
        print(f"\n3. User Data Summary:")
        print(f"   Email: {user_data.get('user', {}).get('email', 'NOT FOUND')}")
        print(f"   First Name: {user_data.get('user', {}).get('first_name', 'NOT FOUND')}")
        print(f"   Last Name: {user_data.get('user', {}).get('last_name', 'NOT FOUND')}")
        print(f"   Username: {user_data.get('user', {}).get('username', 'NOT FOUND')}")
        print(f"   Department: {user_data.get('department', 'NOT FOUND')}")
        print(f"   Job Title: {user_data.get('job_title', 'NOT FOUND')}")
        roles = [r['name'] for r in user_data.get('roles', [])]
        print(f"   Roles: {', '.join(roles) if roles else 'NO ROLES'}")
        print(f"   Profile Photo: {user_data.get('profile_photo', 'NOT FOUND')}")
        
        # 4. Check data structure
        print(f"\n4. Data Structure:")
        print(f"   Has 'user' key: {('user' in user_data)}")
        print(f"   Has 'roles' key: {('roles' in user_data)}")
        print(f"   Has 'permissions' key: {('permissions' in user_data)}")
        print(f"   Has 'modules' key: {('modules' in user_data)}")
        
        return user_data
        
    except Exception as e:
        print(f"\n❌ Error testing {name}: {e}")
        import traceback
        traceback.print_exc()
        return None

# Test both environments
print("\n")
prod_data = test_environment("PRODUCTION", "https://www.radai.ae")
local_data = test_environment("LOCAL", "http://localhost:8000")

# Compare
if prod_data and local_data:
    print("\n" + "=" * 100)
    print("COMPARISON RESULTS")
    print("=" * 100)
    
    prod_email = prod_data.get('user', {}).get('email', '')
    local_email = local_data.get('user', {}).get('email', '')
    
    if prod_email == local_email:
        print(f"\n✅ Both environments return same user: {prod_email}")
    else:
        print(f"\n❌ MISMATCH!")
        print(f"   Production: {prod_email}")
        print(f"   Local: {local_email}")
    
    # Compare structure
    print(f"\nStructure comparison:")
    print(f"   Same data structure: {(prod_data.keys() == local_data.keys())}")
    
    if prod_data.keys() != local_data.keys():
        prod_keys = set(prod_data.keys())
        local_keys = set(local_data.keys())
        print(f"   Missing in local: {prod_keys - local_keys}")
        print(f"   Extra in local: {local_keys - prod_keys}")

print("\n" + "=" * 100)
print("END OF COMPARISON")
print("=" * 100)
