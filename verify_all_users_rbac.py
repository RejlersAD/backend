#!/usr/bin/env python
"""
Advanced RBAC Verification for All Users
Comprehensive analysis using soft coding techniques
"""
import sys
import os
import django
from collections import defaultdict

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule, UserProfile, UserRole
from apps.rbac.rbac_config import ADMIN_ROLE_CODES, SUPERADMIN_ROLE_CODES

User = get_user_model()

print("\n" + "="*80)
print("🔍 COMPREHENSIVE RBAC VERIFICATION - ALL USERS")
print("="*80 + "\n")

# Statistics
stats = {
    'total_users': 0,
    'users_with_profiles': 0,
    'users_without_profiles': 0,
    'users_with_roles': 0,
    'users_without_roles': 0,
    'users_with_modules': 0,
    'users_without_modules': 0,
    'users_with_issues': 0,
    'custom_roles_count': 0,
    'standard_roles_count': 0,
    'admin_users': 0,
    'superadmin_users': 0,
}

issues = {
    'no_profile': [],
    'no_roles': [],
    'no_modules': [],
    'role_without_modules': [],
    'inactive_users_with_roles': [],
}

role_distribution = defaultdict(int)
module_distribution = defaultdict(int)
users_per_module_count = defaultdict(int)

# Get all users
all_users = User.objects.all()
active_users = User.objects.filter(is_active=True)

stats['total_users'] = all_users.count()

print(f"📊 Total Users: {stats['total_users']}")
print(f"✅ Active Users: {active_users.count()}")
print(f"❌ Inactive Users: {stats['total_users'] - active_users.count()}\n")

print("🔄 Analyzing users...")

# Analyze each active user
for idx, user in enumerate(active_users, 1):
    if idx % 50 == 0:
        print(f"   Progress: {idx}/{active_users.count()} users analyzed...")
    
    # Check profile
    try:
        profile = user.rbac_profile
        stats['users_with_profiles'] += 1
        
        # Check roles
        user_roles = UserRole.objects.filter(user_profile=profile)
        
        if not user_roles.exists():
            stats['users_without_roles'] += 1
            issues['no_roles'].append({
                'email': user.email,
                'name': f"{user.first_name} {user.last_name}".strip() or 'N/A'
            })
            continue
        
        stats['users_with_roles'] += 1
        
        # Analyze roles
        has_admin = False
        has_superadmin = False
        
        for user_role in user_roles:
            role = user_role.role
            role_distribution[role.name] += 1
            
            # Check if custom role
            if role.code.startswith('custom_'):
                stats['custom_roles_count'] += 1
            else:
                stats['standard_roles_count'] += 1
            
            # Check admin status
            if role.code.lower() in ADMIN_ROLE_CODES:
                has_admin = True
            if role.code.lower() in SUPERADMIN_ROLE_CODES:
                has_superadmin = True
            
            # Check if role has modules
            role_modules = RoleModule.objects.filter(role=role)
            
            if not role_modules.exists() and not has_admin:
                if user.email not in [u['email'] for u in issues['role_without_modules']]:
                    issues['role_without_modules'].append({
                        'email': user.email,
                        'role': role.name,
                        'role_code': role.code
                    })
        
        if has_admin:
            stats['admin_users'] += 1
        if has_superadmin:
            stats['superadmin_users'] += 1
        
        # Check accessible modules
        accessible_modules = profile.get_all_modules()
        
        if accessible_modules.exists():
            stats['users_with_modules'] += 1
            module_count = accessible_modules.count()
            users_per_module_count[module_count] += 1
            
            # Count module usage
            for module in accessible_modules:
                module_distribution[module.code] += 1
        else:
            stats['users_without_modules'] += 1
            if not has_admin:  # Admins might not need explicit modules
                issues['no_modules'].append({
                    'email': user.email,
                    'roles': ', '.join(ur.role.name for ur in user_roles)
                })
    
    except UserProfile.DoesNotExist:
        stats['users_without_profiles'] += 1
        issues['no_profile'].append({
            'email': user.email,
            'name': f"{user.first_name} {user.last_name}".strip() or 'N/A'
        })

# Check inactive users with roles
inactive_users = User.objects.filter(is_active=False)
for user in inactive_users:
    try:
        profile = user.rbac_profile
        user_roles = UserRole.objects.filter(user_profile=profile)
        if user_roles.exists():
            issues['inactive_users_with_roles'].append({
                'email': user.email,
                'roles': ', '.join(ur.role.name for ur in user_roles)
            })
    except UserProfile.DoesNotExist:
        pass

# Calculate issue count
stats['users_with_issues'] = (
    len(issues['no_profile']) + 
    len(issues['no_roles']) + 
    len(issues['no_modules']) + 
    len(issues['role_without_modules'])
)

# Print Results
print("\n" + "="*80)
print("📈 STATISTICS")
print("="*80)
print(f"✅ Users with profiles: {stats['users_with_profiles']}/{active_users.count()} ({stats['users_with_profiles']/active_users.count()*100:.1f}%)")
print(f"✅ Users with roles: {stats['users_with_roles']}/{active_users.count()} ({stats['users_with_roles']/active_users.count()*100:.1f}%)")
print(f"✅ Users with modules: {stats['users_with_modules']}/{active_users.count()} ({stats['users_with_modules']/active_users.count()*100:.1f}%)")
print(f"\n👥 Role Types:")
print(f"   • Custom Roles: {stats['custom_roles_count']}")
print(f"   • Standard Roles: {stats['standard_roles_count']}")
print(f"   • Admin Users: {stats['admin_users']}")
print(f"   • Super Admin Users: {stats['superadmin_users']}")

# Module distribution
print(f"\n📦 Module Usage (Top 10):")
sorted_modules = sorted(module_distribution.items(), key=lambda x: x[1], reverse=True)
for module_code, count in sorted_modules[:10]:
    percentage = (count / active_users.count()) * 100
    print(f"   • {module_code}: {count} users ({percentage:.1f}%)")

# Users per module count distribution
print(f"\n📊 Module Count Distribution:")
sorted_counts = sorted(users_per_module_count.items())
for module_count, user_count in sorted_counts:
    print(f"   • {user_count} users have access to {module_count} module(s)")

# Issues Report
print(f"\n" + "="*80)
if stats['users_with_issues'] == 0:
    print("✅ RESULT: ALL USERS HAVE PROPER RBAC ASSIGNMENTS")
else:
    print(f"⚠️  RESULT: {stats['users_with_issues']} USER(S) HAVE RBAC ISSUES")

print("="*80)

if issues['no_profile']:
    print(f"\n❌ Users without profiles ({len(issues['no_profile'])}):")
    for issue in issues['no_profile'][:10]:  # Show first 10
        print(f"   • {issue['email']} - {issue['name']}")
    if len(issues['no_profile']) > 10:
        print(f"   ... and {len(issues['no_profile']) - 10} more")

if issues['no_roles']:
    print(f"\n⚠️  Users without roles ({len(issues['no_roles'])}):")
    for issue in issues['no_roles'][:10]:
        print(f"   • {issue['email']} - {issue['name']}")
    if len(issues['no_roles']) > 10:
        print(f"   ... and {len(issues['no_roles']) - 10} more")

if issues['no_modules']:
    print(f"\n⚠️  Users without module access ({len(issues['no_modules'])}):")
    for issue in issues['no_modules'][:10]:
        print(f"   • {issue['email']} (Roles: {issue['roles']})")
    if len(issues['no_modules']) > 10:
        print(f"   ... and {len(issues['no_modules']) - 10} more")

if issues['role_without_modules']:
    print(f"\n⚠️  Roles without modules ({len(issues['role_without_modules'])}):")
    seen = set()
    for issue in issues['role_without_modules'][:10]:
        if issue['role_code'] not in seen:
            print(f"   • {issue['email']} - Role: {issue['role']} ({issue['role_code']})")
            seen.add(issue['role_code'])
    if len(issues['role_without_modules']) > 10:
        print(f"   ... and {len(issues['role_without_modules']) - 10} more")

if issues['inactive_users_with_roles']:
    print(f"\n⚠️  Inactive users with roles ({len(issues['inactive_users_with_roles'])}):")
    for issue in issues['inactive_users_with_roles'][:5]:
        print(f"   • {issue['email']} - {issue['roles']}")
    if len(issues['inactive_users_with_roles']) > 5:
        print(f"   ... and {len(issues['inactive_users_with_roles']) - 5} more")

# Recommendations
print(f"\n" + "="*80)
print("💡 RECOMMENDATIONS")
print("="*80)

if issues['no_profile']:
    print(f"1. Create profiles for {len(issues['no_profile'])} users without profiles")

if issues['no_roles']:
    print(f"2. Assign roles to {len(issues['no_roles'])} users without roles")

if issues['no_modules']:
    print(f"3. Fix module assignments for {len(issues['no_modules'])} users")
    print(f"   Run: python manage.py fix_rbac_assignments")

if issues['role_without_modules']:
    unique_roles = len(set(i['role_code'] for i in issues['role_without_modules']))
    print(f"4. Link modules to {unique_roles} custom roles without modules")
    print(f"   Run: python manage.py fix_rbac_assignments")

if stats['users_with_issues'] == 0:
    print("✅ No action required - all users have proper RBAC assignments!")

# Role distribution details
print(f"\n" + "="*80)
print("👥 ROLE DISTRIBUTION (Top 15)")
print("="*80)
sorted_roles = sorted(role_distribution.items(), key=lambda x: x[1], reverse=True)
for role_name, count in sorted_roles[:15]:
    print(f"   • {role_name}: {count} user(s)")

if len(sorted_roles) > 15:
    print(f"   ... and {len(sorted_roles) - 15} more roles")

print("\n" + "="*80 + "\n")

# Summary
print("📋 SUMMARY:")
print(f"   ✅ Verified: {active_users.count()} active users")
print(f"   ✅ Healthy: {active_users.count() - stats['users_with_issues']} users")
print(f"   ⚠️  Issues: {stats['users_with_issues']} users")
print(f"   📊 Health Score: {((active_users.count() - stats['users_with_issues']) / active_users.count() * 100):.1f}%")
print("\n" + "="*80 + "\n")
