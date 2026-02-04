#!/usr/bin/env python
"""
Compare access between two users to understand permission differences
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserRole, Role, Module

User = get_user_model()

def analyze_user_access(email):
    """Analyze a user's complete access profile"""
    try:
        user = User.objects.filter(email__iexact=email).first()
        
        if not user:
            return None
        
        data = {
            'email': user.email,
            'name': f"{user.first_name} {user.last_name}",
            'is_active': user.is_active,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'roles': [],
            'modules': set(),
            'permissions': []
        }
        
        # Check RBAC profile
        try:
            profile = user.rbac_profile
            data['organization'] = profile.organization.name if profile.organization else 'None'
            data['is_deleted'] = profile.is_deleted
            
            # Get all roles
            user_roles = UserRole.objects.filter(
                user_profile=profile
            ).select_related('role').prefetch_related('role__modules')
            
            for user_role in user_roles:
                role = user_role.role
                role_data = {
                    'name': role.name,
                    'description': role.description,
                    'modules': []
                }
                
                for module in role.modules.all():
                    role_data['modules'].append(module.name)
                    data['modules'].add(module.name)
                
                data['roles'].append(role_data)
            
            # Check Django permissions
            if user.is_superuser:
                data['permissions'].append('ALL (Superuser)')
            else:
                perms = user.get_all_permissions()
                data['permissions'] = list(perms)
                
        except Exception as e:
            data['profile_error'] = str(e)
        
        return data
        
    except Exception as e:
        print(f"❌ Error analyzing {email}: {e}")
        return None

def compare_users(email1, email2):
    """Compare two users' access"""
    print(f"\n{'='*80}")
    print(f"USER ACCESS COMPARISON")
    print(f"{'='*80}\n")
    
    user1 = analyze_user_access(email1)
    user2 = analyze_user_access(email2)
    
    if not user1:
        print(f"❌ User 1 not found: {email1}")
        return
    
    if not user2:
        print(f"❌ User 2 not found: {email2}")
        return
    
    # Display User 1
    print(f"👤 USER 1: {user1['email']}")
    print(f"{'─'*80}")
    print(f"Name: {user1['name']}")
    print(f"Organization: {user1.get('organization', 'N/A')}")
    print(f"Is Active: {user1['is_active']}")
    print(f"Is Staff: {user1['is_staff']}")
    print(f"Is Superuser: {user1['is_superuser']}")
    print(f"Is Deleted: {user1.get('is_deleted', 'N/A')}")
    
    print(f"\n📋 Roles ({len(user1['roles'])}):")
    if not user1['roles']:
        print("   ⚠️  No roles assigned")
    else:
        for role in user1['roles']:
            print(f"   • {role['name']}")
            if role['modules']:
                for mod in role['modules']:
                    print(f"      - {mod}")
    
    print(f"\n🔐 Django Permissions ({len(user1['permissions'])}):")
    if user1['is_superuser']:
        print("   ✅ SUPERUSER - Has all permissions")
    elif not user1['permissions']:
        print("   ⚠️  No Django permissions")
    else:
        for perm in sorted(user1['permissions'])[:10]:  # Show first 10
            print(f"   • {perm}")
        if len(user1['permissions']) > 10:
            print(f"   ... and {len(user1['permissions']) - 10} more")
    
    print(f"\n📦 Accessible Modules ({len(user1['modules'])}):")
    if not user1['modules']:
        print("   ⚠️  No modules accessible")
    else:
        for mod in sorted(user1['modules']):
            print(f"   ✅ {mod}")
    
    # Display User 2
    print(f"\n{'='*80}")
    print(f"👤 USER 2: {user2['email']}")
    print(f"{'─'*80}")
    print(f"Name: {user2['name']}")
    print(f"Organization: {user2.get('organization', 'N/A')}")
    print(f"Is Active: {user2['is_active']}")
    print(f"Is Staff: {user2['is_staff']}")
    print(f"Is Superuser: {user2['is_superuser']}")
    print(f"Is Deleted: {user2.get('is_deleted', 'N/A')}")
    
    print(f"\n📋 Roles ({len(user2['roles'])}):")
    if not user2['roles']:
        print("   ⚠️  No roles assigned")
    else:
        for role in user2['roles']:
            print(f"   • {role['name']}")
            if role['modules']:
                for mod in role['modules']:
                    print(f"      - {mod}")
    
    print(f"\n🔐 Django Permissions ({len(user2['permissions'])}):")
    if user2['is_superuser']:
        print("   ✅ SUPERUSER - Has all permissions")
    elif not user2['permissions']:
        print("   ⚠️  No Django permissions")
    else:
        for perm in sorted(user2['permissions'])[:10]:  # Show first 10
            print(f"   • {perm}")
        if len(user2['permissions']) > 10:
            print(f"   ... and {len(user2['permissions']) - 10} more")
    
    print(f"\n📦 Accessible Modules ({len(user2['modules'])}):")
    if not user2['modules']:
        print("   ⚠️  No modules accessible")
    else:
        for mod in sorted(user2['modules']):
            print(f"   ✅ {mod}")
    
    # Compare differences
    print(f"\n{'='*80}")
    print(f"🔍 KEY DIFFERENCES")
    print(f"{'='*80}")
    
    # Module differences
    user1_only = user1['modules'] - user2['modules']
    user2_only = user2['modules'] - user1['modules']
    common = user1['modules'] & user2['modules']
    
    print(f"\n📦 Module Access:")
    print(f"   Common modules: {len(common)}")
    if user1_only:
        print(f"   User 1 only ({len(user1_only)}):")
        for mod in sorted(user1_only):
            print(f"      • {mod}")
    if user2_only:
        print(f"   User 2 only ({len(user2_only)}):")
        for mod in sorted(user2_only):
            print(f"      • {mod}")
    
    # Privilege differences
    print(f"\n👑 Privilege Level:")
    if user1['is_superuser'] and not user2['is_superuser']:
        print(f"   ⚠️  User 1 is SUPERUSER, User 2 is not")
    elif user2['is_superuser'] and not user1['is_superuser']:
        print(f"   ⚠️  User 2 is SUPERUSER, User 1 is not")
    elif user1['is_superuser'] and user2['is_superuser']:
        print(f"   ✅ Both users are SUPERUSER")
    else:
        print(f"   ✅ Neither user is SUPERUSER")
    
    if user1['is_staff'] and not user2['is_staff']:
        print(f"   ℹ️  User 1 is STAFF, User 2 is not")
    elif user2['is_staff'] and not user1['is_staff']:
        print(f"   ℹ️  User 2 is STAFF, User 1 is not")
    
    # Role differences
    print(f"\n📋 Role Count:")
    print(f"   User 1: {len(user1['roles'])} roles")
    print(f"   User 2: {len(user2['roles'])} roles")
    
    print(f"\n{'='*80}\n")

if __name__ == '__main__':
    # Get all superusers first
    print("\n🔍 Finding all superusers and staff users...")
    print(f"{'='*80}")
    
    superusers = User.objects.filter(is_superuser=True, is_active=True)
    print(f"\n👑 Superusers ({superusers.count()}):")
    for su in superusers:
        print(f"   • {su.email} - {su.first_name} {su.last_name}")
    
    staff_users = User.objects.filter(is_staff=True, is_active=True).exclude(is_superuser=True)
    print(f"\n👨‍💼 Staff Users (non-superuser) ({staff_users.count()}):")
    for staff in staff_users[:10]:  # Show first 10
        print(f"   • {staff.email} - {staff.first_name} {staff.last_name}")
    if staff_users.count() > 10:
        print(f"   ... and {staff_users.count() - 10} more")
    
    print(f"\n{'='*80}")
    print("\n💡 Please provide YOUR email to compare with muhammad.ilyas@rejlers.ae")
    print("   Common admin emails: tanzeem.agra@rejlers.ae, mohammed.agra@rejlers.ae")
    print()
    
    # For now, let's compare with the first superuser if available
    if superusers.exists():
        admin_email = superusers.first().email
        user_email = "muhammad.ilyas@rejlers.ae"
        
        print(f"📊 Comparing: {admin_email} vs {user_email}\n")
        compare_users(admin_email, user_email)
