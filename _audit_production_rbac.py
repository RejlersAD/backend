"""
RBAC Security Audit Script - Production Database
================================================
Run this script against the production database to identify users with unauthorized
access to sensitive modules (Payroll, HR, Finance, Procurement, Sales).

Usage:
  python _audit_production_rbac.py

Requirements:
  - Railway CLI: `railway login` and `railway link` to production
  - Or direct database connection string in .env
"""

import os
import sys
import django
from pathlib import Path

# Setup Django
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, Module, UserProfile
import json

User = get_user_model()

# Sensitive module codes from rbac_config.py
SENSITIVE_MODULES = {
    'hr': ['payroll', 'hr_management', 'timesheet', 'hr_onboarding'],
    'finance': ['finance'],
    'sales': ['sales'],
    'procurement': ['procurement', 'procurement_vendors', 'procurement_orders', 
                    'procurement_requisitions', 'procurement_receipts']
}

AUTHORIZED_ROLES = ['super_admin', 'hr_admin', 'admin']

def run_query(query, description):
    """Execute query and return results"""
    print(f"\n{'='*80}")
    print(f"🔍 {description}")
    print(f"{'='*80}\n")
    
    with connection.cursor() as cursor:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        results = cursor.fetchall()
        
        if results:
            # Print column headers
            print("  ".join(f"{col:20}" for col in columns))
            print("-" * 80)
            
            # Print rows
            for row in results:
                print("  ".join(f"{str(val)[:20]:20}" for val in row))
            
            print(f"\n✅ Found {len(results)} records\n")
        else:
            print("✅ No records found (GOOD - no unauthorized access)\n")
        
        return results, columns


def audit_sensitive_module_access(category, module_codes):
    """Check who has access to specific sensitive modules"""
    query = f"""
    SELECT 
        u.email,
        u.first_name,
        u.last_name,
        r.name as role_name,
        r.code as role_code,
        m.code as module_code,
        up.department,
        up.status
    FROM auth_user u
    JOIN rbac_userprofile up ON u.id = up.user_id
    JOIN rbac_userrole ur ON up.id = ur.user_profile_id
    JOIN rbac_role r ON ur.role_id = r.id
    JOIN rbac_rolemodule rm ON r.id = rm.role_id
    JOIN rbac_module m ON rm.module_id = m.id
    WHERE 
        m.code IN ({','.join(["'" + code + "'" for code in module_codes])})
        AND up.is_deleted = false
        AND r.is_active = true
        AND ur.is_primary = true
    ORDER BY m.code, u.email
    """
    
    results, columns = run_query(query, f"Users with {category.upper()} module access")
    
    # Analyze results
    unauthorized = []
    for row in results:
        email, first_name, last_name, role_name, role_code, module_code, department, status = row
        
        # Check if role is authorized for this sensitive module
        if role_code not in AUTHORIZED_ROLES:
            # For HR modules, only super_admin and hr_admin are allowed
            if category == 'hr' and role_code != 'hr_admin':
                unauthorized.append({
                    'email': email,
                    'name': f"{first_name} {last_name}",
                    'role': role_name,
                    'role_code': role_code,
                    'module': module_code,
                    'department': department,
                    'issue': f'❌ UNAUTHORIZED: {category.upper()} module access without proper role'
                })
    
    return unauthorized


def check_default_role_users():
    """Check if any Default role users have sensitive module access"""
    query = """
    SELECT 
        u.email,
        u.first_name,
        u.last_name,
        STRING_AGG(m.code, ', ' ORDER BY m.code) as all_modules,
        STRING_AGG(
            CASE 
                WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
                THEN m.code 
            END, ', ') as sensitive_modules
    FROM auth_user u
    JOIN rbac_userprofile up ON u.id = up.user_id
    JOIN rbac_userrole ur ON up.id = ur.user_profile_id
    JOIN rbac_role r ON ur.role_id = r.id
    LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
    LEFT JOIN rbac_module m ON rm.module_id = m.id
    WHERE 
        up.is_deleted = false
        AND r.is_active = true
        AND r.code = 'default'
        AND ur.is_primary = true
    GROUP BY u.email, u.first_name, u.last_name
    HAVING 
        STRING_AGG(
            CASE 
                WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
                THEN m.code 
            END, ', ') IS NOT NULL
    ORDER BY u.email
    """
    
    results, columns = run_query(query, "Default role users with SENSITIVE module access (SHOULD BE EMPTY)")
    
    vulnerabilities = []
    for row in results:
        email, first_name, last_name, all_modules, sensitive_modules = row
        vulnerabilities.append({
            'email': email,
            'name': f"{first_name} {last_name}",
            'role': 'Default',
            'all_modules': all_modules,
            'sensitive_modules': sensitive_modules,
            'issue': '🚨 CRITICAL: Default role user has sensitive module access'
        })
    
    return vulnerabilities


def check_specific_user(email):
    """Check specific user's module access"""
    query = f"""
    SELECT 
        u.email,
        u.first_name,
        u.last_name,
        u.is_superuser,
        u.is_staff,
        STRING_AGG(DISTINCT r.name, ', ') as roles,
        STRING_AGG(DISTINCT r.code, ', ') as role_codes,
        STRING_AGG(DISTINCT m.code, ', ' ORDER BY m.code) as modules
    FROM auth_user u
    JOIN rbac_userprofile up ON u.id = up.user_id
    JOIN rbac_userrole ur ON up.id = ur.user_profile_id
    JOIN rbac_role r ON ur.role_id = r.id
    LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
    LEFT JOIN rbac_module m ON rm.module_id = m.id
    WHERE 
        u.email = '{email}'
        AND up.is_deleted = false
        AND r.is_active = true
    GROUP BY u.id, u.email, u.first_name, u.last_name, u.is_superuser, u.is_staff
    """
    
    results, columns = run_query(query, f"Module access for user: {email}")
    return results


def generate_security_report():
    """Generate comprehensive security audit report"""
    print("\n" + "="*80)
    print("🔐 RAD AI PRODUCTION DATABASE - RBAC SECURITY AUDIT")
    print("="*80)
    print("Purpose: Identify unauthorized access to sensitive modules")
    print("Date:", __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("="*80)
    
    all_vulnerabilities = []
    
    # Check each sensitive module category
    for category, module_codes in SENSITIVE_MODULES.items():
        unauthorized = audit_sensitive_module_access(category, module_codes)
        all_vulnerabilities.extend(unauthorized)
    
    # Check Default role users
    default_role_vulns = check_default_role_users()
    all_vulnerabilities.extend(default_role_vulns)
    
    # Check specific user mentioned in ticket
    print("\n" + "="*80)
    print("🎯 SPECIFIC USER CHECK: Debasis.Sana@rejlers.ae")
    print("="*80)
    check_specific_user('Debasis.Sana@rejlers.ae')
    
    # Generate summary report
    print("\n" + "="*80)
    print("📊 SECURITY AUDIT SUMMARY")
    print("="*80)
    
    if all_vulnerabilities:
        print(f"\n🚨 FOUND {len(all_vulnerabilities)} SECURITY VULNERABILITIES\n")
        
        # Print vulnerabilities
        print(f"{'Email':<30} {'Name':<25} {'Role':<20} {'Module':<25} {'Issue':<40}")
        print("-" * 140)
        for v in all_vulnerabilities:
            print(f"{v.get('email', ''):<30} "
                  f"{v.get('name', ''):<25} "
                  f"{v.get('role', ''):<20} "
                  f"{v.get('module', v.get('sensitive_modules', '')):<25} "
                  f"{v.get('issue', ''):<40}")
        
        # Save to file
        report_file = BASE_DIR / '_rbac_security_audit_report.json'
        with open(report_file, 'w') as f:
            json.dump(all_vulnerabilities, f, indent=2)
        print(f"\n💾 Full report saved to: {report_file}")
        
        print("\n⚠️  ACTION REQUIRED:")
        print("  1. Remove sensitive module access from unauthorized users")
        print("  2. Deploy backend RBAC permission fixes to production")
        print("  3. Re-run this audit to verify fixes")
    else:
        print("\n✅ NO VULNERABILITIES FOUND")
        print("All users have appropriate module access based on their roles.")
    
    print("\n" + "="*80)


if __name__ == '__main__':
    try:
        generate_security_report()
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
