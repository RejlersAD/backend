#!/usr/bin/env python
"""
Check if user exists in database and verify credentials
"""
import os
import sys
import django

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DATABASE_URL'] = 'postgresql://postgres:cJLHOrfvZxZXHKaMCWdLdRedgHgmIneU@shinkansen.proxy.rlwy.net:38534/railway'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    django.setup()
    
    from django.contrib.auth import get_user_model, authenticate
    
    User = get_user_model()
    
    print("\n" + "="*60)
    print("🔍 CHECKING USER IN DATABASE")
    print("="*60)
    
    email = 'tanzeem.agra@rejlers.ae'
    password = 'Tanzeem@123'
    
    # Check if user exists
    user = User.objects.filter(email=email).first()
    
    if user:
        print(f"✅ User found!")
        print(f"   Email: {user.email}")
        print(f"   Username: {user.username}")
        print(f"   Is active: {user.is_active}")
        print(f"   Is staff: {user.is_staff}")
        print(f"   Is superuser: {user.is_superuser}")
        print(f"   Has usable password: {user.has_usable_password()}")
        print(f"   Date joined: {user.date_joined}")
        
        # Test authentication
        print("\n🔐 Testing authentication...")
        auth_user = authenticate(username=email, password=password)
        
        if auth_user:
            print("✅ Authentication SUCCESSFUL!")
            print(f"   Authenticated as: {auth_user.email}")
        else:
            print("❌ Authentication FAILED!")
            print("   Password might be incorrect")
            
            # Try to set the password
            print("\n🔧 Attempting to reset password to 'Tanzeem@123'...")
            user.set_password(password)
            user.save()
            print("✅ Password reset successful!")
            
            # Test again
            auth_user = authenticate(username=email, password=password)
            if auth_user:
                print("✅ Authentication now WORKS!")
            else:
                print("❌ Still failed - check Django settings")
    else:
        print(f"❌ User NOT found: {email}")
        print("\n🔧 Creating user...")
        
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name='Tanzeem',
            last_name='Agra',
            is_active=True,
            is_staff=True,
            is_superuser=True
        )
        print("✅ User created successfully!")
        print(f"   Email: {user.email}")
        print(f"   Password: Tanzeem@123")
        
    print("="*60)
    print("✅ Database check complete!")
    print("="*60)
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
