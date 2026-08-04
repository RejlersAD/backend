"""
Direct SQL script to create RadAI managers in production
Uses DATABASE_URL from Railway environment
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid
from datetime import datetime

# Get DATABASE_URL from environment (set by railway shell)
# Prefer PUBLIC URL for external access
DATABASE_URL = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL not found!")
    print("   Run this script inside 'railway shell':")
    print("   railway shell")
    print("   python create_managers_direct_sql.py")
    exit(1)

print("\n" + "="*80)
print("CREATING RADAI MANAGERS IN PRODUCTION DATABASE")
print("="*80)

MANAGERS = [
    {
        'email': 'rafat.sm.saqer@rejlers.ae',
        'username': 'rafat.saqer',
        'first_name': 'Rafat',
        'last_name': 'S. M. Saqer',
        'department': 'radai',
        'job_title': 'Manager'
    },
    {
        'email': 'anam.abbas@rejlers.ae',
        'username': 'anam.abbas',
        'first_name': 'Anam',
        'last_name': 'Abbas',
        'department': 'radai',
        'job_title': 'Manager'
    },
    {
        'email': 'aleksi.murtomaki@rejlers.ae',
        'username': 'aleksi.murtomaki',
        'first_name': 'Aleksi',
        'last_name': 'Murtomaki',
        'department': 'radai',
        'job_title': 'Manager'
    }
]

try:
    # Connect to production database
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    print(f"\n✅ Connected to database: {DATABASE_URL.split('@')[1].split('/')[0]}")
    
    # Get organization
    cur.execute("SELECT id, name FROM rbac_organizations WHERE is_active = true ORDER BY created_at LIMIT 1")
    org = cur.fetchone()
    
    if not org:
        print("❌ No active organization found!")
        exit(1)
    
    print(f"📍 Organization: {org['name']} ({org['id']})")
    
    print("\n" + "-"*80)
    print("CREATING/UPDATING MANAGERS")
    print("-"*80)
    
    created_count = 0
    updated_count = 0
    
    for mgr in MANAGERS:
        print(f"\n👤 {mgr['first_name']} {mgr['last_name']} ({mgr['email']})")
        
        # Check if user exists
        cur.execute("SELECT id, is_active FROM users WHERE email = %s", (mgr['email'],))
        user = cur.fetchone()
        
        if user:
            # Update user
            cur.execute("""
                UPDATE users 
                SET username = %s, first_name = %s, last_name = %s, is_active = true
                WHERE email = %s
                RETURNING id
            """, (mgr['username'], mgr['first_name'], mgr['last_name'], mgr['email']))
            user_id = cur.fetchone()['id']
            print(f"   ✅ User updated (ID: {user_id})")
        else:
            # Create user
            user_id = str(uuid.uuid4())
            now = datetime.now()
            cur.execute("""
                INSERT INTO users (id, password, last_login, is_superuser, username, first_name, 
                                       last_name, email, is_staff, is_active, date_joined)
                VALUES (%s, '', NULL, false, %s, %s, %s, %s, false, true, %s)
                RETURNING id
            """, (user_id, mgr['username'], mgr['first_name'], mgr['last_name'], 
                  mgr['email'], now))
            user_id = cur.fetchone()['id']
            print(f"   ✅ User created (ID: {user_id})")
            created_count += 1
        
        # Check if profile exists
        cur.execute("SELECT id FROM rbac_user_profiles WHERE user_id = %s", (user_id,))
        profile = cur.fetchone()
        
        now = datetime.now()
        
        if profile:
            # Update profile
            cur.execute("""
                UPDATE rbac_user_profiles 
                SET organization_id = %s, department = %s, job_title = %s, 
                    status = 'active', is_deleted = false, updated_at = %s
                WHERE user_id = %s
            """, (org['id'], mgr['department'], mgr['job_title'], now, user_id))
            print(f"   🔄 Profile updated: department={mgr['department']}, status=active")
            updated_count += 1
        else:
            # Create profile
            profile_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO rbac_user_profiles (id, user_id, organization_id, department, 
                                              job_title, status, is_deleted, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'active', false, %s, %s)
            """, (profile_id, user_id, org['id'], mgr['department'], 
                  mgr['job_title'], now, now))
            print(f"   ✅ Profile created: department={mgr['department']}, status=active")
            created_count += 1
    
    # Commit changes
    conn.commit()
    
    print("\n" + "-"*80)
    print("VERIFICATION")
    print("-"*80)
    
    for mgr in MANAGERS:
        cur.execute("""
            SELECT u.email, u.first_name, u.last_name, u.is_active,
                   p.department, p.job_title, p.status, p.is_deleted,
                   o.name as org_name
            FROM users u
            JOIN rbac_user_profiles p ON p.user_id = u.id
            JOIN rbac_organizations o ON o.id = p.organization_id
            WHERE u.email = %s
        """, (mgr['email'],))
        
        result = cur.fetchone()
        if result:
            print(f"\n✅ {result['first_name']} {result['last_name']} ({result['email']})")
            print(f"   Organization: {result['org_name']}")
            print(f"   Department: {result['department']} | Job Title: {result['job_title']}")
            print(f"   Status: {result['status']} | Active: {result['is_active']} | Deleted: {result['is_deleted']}")
        else:
            print(f"❌ {mgr['email']} - NOT FOUND")
    
    print("\n" + "="*80)
    print("COMPLETION SUMMARY")
    print("="*80)
    print(f"Profiles created:    {created_count}")
    print(f"Profiles updated:    {updated_count}")
    print(f"Total managers:      {len(MANAGERS)}")
    print(f"Organization:        {org['name']}")
    print("="*80)
    print("✅ SUCCESS! Managers are now in production database!")
    print("   Visit https://www.radai.ae/profile to verify")
    print("="*80 + "\n")
    
    cur.close()
    conn.close()

except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
