#!/usr/bin/env python
"""
Production RBAC Audit - Find All Users with Permission Issues
==============================================================
This script finds ALL users who have Django admin flags (is_superuser, is_staff)
but should not have them based on their RBAC roles.

Usage:
    In Railway Shell:
        python audit_production_rbac_issues.py
    
    Local (connecting to production):
        python audit_production_rbac_issues.py --local
"""

import os
import sys
import django
from datetime import datetime

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import models
from apps.rbac.models import UserProfile, UserRole, Role

User = get_user_model()

# ============================================================
# CONFIGURATION
# ============================================================

# Roles that are AUTHORIZED to have Django staff/superuser flags
AUTHORIZED_ADMIN_ROLES = [
    'super_admin',
    'admin', 
    'ict_admin',
]

# Known authorized admin users (skip these in audit)
KNOWN_AUTHORIZED_ADMINS = [
    'mohammed.agra@rejlers.ae',
    'fahad.hussein@rejlers.ae',
    'tanzeem.agra@rejlers.ae',
]

# ============================================================
# AUDIT FUNCTIONS
# ============================================================

def print_header(message):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {message}")
    print("=" * 80)


def print_separator():
    """Print separator line"""
    print("-" * 80)


def audit_user_flags():
    """
    Find all users who have Django flags but shouldn't
    Returns: dict with categorized users
    """
    print_header("🔍 PRODUCTION RBAC AUDIT - STARTED")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Get all active users with Django flags set
    users_with_flags = User.objects.filter(
        is_active=True
    ).filter(
        models.Q(is_superuser=True) | models.Q(is_staff=True)
    ).select_related('rbac_profile')
    
    print(f"📊 Total active users with Django flags: {users_with_flags.count()}")
    print()
    
    results = {
        'critical': [],      # is_superuser=True without admin role
        'warning': [],       # is_staff=True without admin role
        'authorized': [],    # Has admin role - OK
        'errors': [],        # Issues checking user
    }
    
    for user in users_with_flags:
        try:
            # Get user's RBAC profile and roles
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            user_roles = UserRole.objects.filter(
                user_profile=profile,
                role__is_active=True
            ).select_related('role')
            
            role_codes = [ur.role.code for ur in user_roles]
            primary_role = next((ur.role for ur in user_roles if ur.is_primary), None)
            
            # Check if user has authorized admin role
            has_admin_role = any(code in AUTHORIZED_ADMIN_ROLES for code in role_codes)
            
            user_info = {
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_superuser': user.is_superuser,
                'is_staff': user.is_staff,
                'primary_role': primary_role.code if primary_role else 'None',
                'all_roles': ', '.join(role_codes) if role_codes else 'None',
                'last_login': user.last_login.strftime('%Y-%m-%d %H:%M') if user.last_login else 'Never',
                'date_joined': user.date_joined.strftime('%Y-%m-%d'),
            }
            
            # Skip known authorized admins
            if user.email.lower() in [e.lower() for e in KNOWN_AUTHORIZED_ADMINS]:
                user_info['note'] = 'Known authorized admin - skipped'
                results['authorized'].append(user_info)
                continue
            
            # Categorize user based on flags and roles
            if user.is_superuser and not has_admin_role:
                user_info['issue'] = 'CRITICAL: is_superuser=True without admin role'
                user_info['severity'] = '🔴 CRITICAL'
                results['critical'].append(user_info)
            
            elif user.is_staff and not has_admin_role:
                user_info['issue'] = 'WARNING: is_staff=True without admin role'
                user_info['severity'] = '🟡 WARNING'
                results['warning'].append(user_info)
            
            else:
                user_info['note'] = 'Has authorized admin role'
                results['authorized'].append(user_info)
        
        except UserProfile.DoesNotExist:
            results['errors'].append({
                'email': user.email,
                'error': 'UserProfile not found or deleted'
            })
        except Exception as e:
            results['errors'].append({
                'email': user.email,
                'error': str(e)
            })
    
    return results


def print_results(results):
    """Print formatted audit results"""
    
    # CRITICAL ISSUES
    if results['critical']:
        print_header(f"🔴 CRITICAL ISSUES ({len(results['critical'])} users)")
        print("These users have is_superuser=True but NO admin RBAC role")
        print("ACTION: Remove is_superuser flag immediately")
        print()
        
        for i, user in enumerate(results['critical'], 1):
            print(f"{i}. {user['email']}")
            print(f"   Name: {user['first_name']} {user['last_name']}")
            print(f"   is_superuser: {user['is_superuser']} | is_staff: {user['is_staff']}")
            print(f"   RBAC Role: {user['primary_role']} (All: {user['all_roles']})")
            print(f"   Last Login: {user['last_login']}")
            print(f"   Joined: {user['date_joined']}")
            print(f"   Issue: {user['issue']}")
            print()
    
    # WARNING ISSUES  
    if results['warning']:
        print_header(f"🟡 WARNING ISSUES ({len(results['warning'])} users)")
        print("These users have is_staff=True but NO admin RBAC role")
        print("ACTION: Review and remove is_staff flag if not needed")
        print()
        
        for i, user in enumerate(results['warning'], 1):
            print(f"{i}. {user['email']}")
            print(f"   Name: {user['first_name']} {user['last_name']}")
            print(f"   is_superuser: {user['is_superuser']} | is_staff: {user['is_staff']}")
            print(f"   RBAC Role: {user['primary_role']} (All: {user['all_roles']})")
            print(f"   Last Login: {user['last_login']}")
            print(f"   Issue: {user['issue']}")
            print()
    
    # AUTHORIZED USERS (OK)
    if results['authorized']:
        print_header(f"✅ AUTHORIZED USERS ({len(results['authorized'])} users)")
        print("These users have Django flags AND authorized admin RBAC roles - OK")
        print()
        
        for i, user in enumerate(results['authorized'], 1):
            print(f"{i}. {user['email']} - {user['primary_role']} - {user.get('note', 'OK')}")
        print()
    
    # ERRORS
    if results['errors']:
        print_header(f"❌ ERRORS ({len(results['errors'])} users)")
        print("Could not check these users due to errors")
        print()
        
        for i, error in enumerate(results['errors'], 1):
            print(f"{i}. {error['email']}: {error['error']}")
        print()
    
    # SUMMARY
    print_header("📊 SUMMARY")
    print(f"🔴 Critical Issues:    {len(results['critical'])} users")
    print(f"🟡 Warning Issues:     {len(results['warning'])} users")
    print(f"✅ Authorized (OK):    {len(results['authorized'])} users")
    print(f"❌ Errors:             {len(results['errors'])} users")
    print()
    
    total_issues = len(results['critical']) + len(results['warning'])
    if total_issues > 0:
        print(f"⚠️  TOTAL USERS NEEDING FIX: {total_issues}")
    else:
        print("✅ No issues found - all users correctly configured!")
    print()


def generate_fix_list(results):
    """Generate list of emails to fix"""
    print_header("🔧 USERS TO FIX")
    
    all_issues = results['critical'] + results['warning']
    
    if not all_issues:
        print("No users need fixing!")
        return
    
    print("Copy this list to fix_custom_role_permissions.py:")
    print()
    print("USERS_TO_FIX = [")
    for user in all_issues:
        print(f'    "{user["email"]}",  # {user["primary_role"]} - {user["severity"]}')
    print("]")
    print()
    
    print("Or run this SQL in Railway Database Console:")
    print()
    print("UPDATE auth_user")
    print("SET is_superuser = false, is_staff = false")
    print("WHERE email IN (")
    for i, user in enumerate(all_issues):
        comma = "," if i < len(all_issues) - 1 else ""
        print(f"    '{user['email']}'{comma}")
    print(");")
    print()


def generate_csv_export(results):
    """Generate CSV export of issues"""
    import csv
    from io import StringIO
    
    all_issues = results['critical'] + results['warning']
    
    if not all_issues:
        return
    
    print_header("📄 CSV EXPORT")
    
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'email', 'first_name', 'last_name', 'severity', 
        'is_superuser', 'is_staff', 'primary_role', 'all_roles',
        'last_login', 'date_joined', 'issue'
    ])
    
    writer.writeheader()
    for user in all_issues:
        writer.writerow(user)
    
    csv_content = output.getvalue()
    
    # Save to file
    filename = f'rbac_issues_production_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        f.write(csv_content)
    
    print(f"✅ CSV exported to: {filename}")
    print(f"   Total rows: {len(all_issues)}")
    print()


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    """Main execution"""
    
    try:
        # Run audit
        results = audit_user_flags()
        
        # Print results
        print_results(results)
        
        # Generate fix list
        generate_fix_list(results)
        
        # Export CSV
        try:
            generate_csv_export(results)
        except Exception as e:
            print(f"⚠️  Could not export CSV: {e}")
        
        print_header("✅ AUDIT COMPLETE")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Exit with appropriate code
        total_issues = len(results['critical']) + len(results['warning'])
        if total_issues > 0:
            print(f"⚠️  Found {total_issues} users with permission issues")
            print("   Use the generated SQL or Python script to fix them")
            sys.exit(1)  # Exit with error code to indicate issues found
        else:
            print("✅ No issues found!")
            sys.exit(0)
    
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == '__main__':
    main()
