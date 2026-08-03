"""
Fix Django user flags for kiran.ingale@rejlers.ae
Run in Railway shell: python fix_kiran_django_flags.py

CRITICAL FIX: Frontend checks is_staff and is_superuser flags
If either is True, frontend bypasses ALL RBAC and shows everything
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()

def fix_django_flags():
    email = 'kiran.ingale@rejlers.ae'
    
    print("=" * 80)
    print(f"🔧 FIXING DJANGO USER FLAGS")
    print(f"   User: {email}")
    print("=" * 80)
    
    try:
        user = User.objects.get(email=email)
        
        print(f"\n📋 CURRENT STATE:")
        print(f"   is_staff: {user.is_staff}")
        print(f"   is_superuser: {user.is_superuser}")
        
        needs_fix = user.is_staff or user.is_superuser
        
        if not needs_fix:
            print(f"\n✅ FLAGS ALREADY CORRECT")
            print(f"   No changes needed")
            return
        
        print(f"\n⚠️  PROBLEM DETECTED:")
        if user.is_staff:
            print(f"   • is_staff=True → Frontend treats user as admin")
            print(f"   • Frontend bypasses RBAC and shows ALL modules")
        if user.is_superuser:
            print(f"   • is_superuser=True → Backend AND frontend bypass")
            print(f"   • User has unrestricted system access")
        
        print(f"\n🔄 APPLYING FIX...")
        
        with transaction.atomic():
            # Save original values for audit
            original_staff = user.is_staff
            original_superuser = user.is_superuser
            
            # Set both flags to False
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=['is_staff', 'is_superuser'])
            
            print(f"   ✅ is_staff: {original_staff} → False")
            print(f"   ✅ is_superuser: {original_superuser} → False")
        
        print(f"\n✅ FIX APPLIED SUCCESSFULLY")
        
        # Verify
        user.refresh_from_db()
        print(f"\n📋 VERIFICATION:")
        print(f"   is_staff: {user.is_staff}")
        print(f"   is_superuser: {user.is_superuser}")
        
        if not user.is_staff and not user.is_superuser:
            print(f"\n✅ VERIFIED: Both flags are now False")
        else:
            print(f"\n❌ ERROR: Flags not updated correctly")
            return
        
        # Instructions
        print(f"\n" + "=" * 80)
        print(f"📋 NEXT STEPS:")
        print("=" * 80)
        print(f"\n1️⃣  Check for RBAC admin roles:")
        print(f"   python check_all_admin_flags.py")
        print(f"\n2️⃣  If user has admin/ict_admin role, remove it:")
        print(f"   python fix_kiran_production.py")
        print(f"\n3️⃣  Tell user to:")
        print(f"   • Logout from https://www.radai.ae")
        print(f"   • Login again (refreshes JWT token)")
        print(f"   • Hard refresh browser (Ctrl+F5)")
        print(f"\n4️⃣  Verify user CANNOT see:")
        print(f"   ❌ Finance")
        print(f"   ❌ QHSE")
        print(f"   ❌ Human Resources")
        print(f"   ❌ Admin")
        print()
        
    except User.DoesNotExist:
        print(f"❌ User '{email}' not found")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    fix_django_flags()
