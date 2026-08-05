"""
Simple RBAC Audit Script - Direct SQL Queries
Run this against production database to find unauthorized access
"""

import os
import sys
import psycopg2
from pathlib import Path

def get_db_connection():
    """Get database connection from environment"""
    db_url = os.getenv('DATABASE_URL')

    if not db_url:
        sys.exit('DATABASE_URL environment variable is not set.')

    return psycopg2.connect(db_url)


def run_audit():
    print("\n" + "="*80)
    print(" RBAC SECURITY AUDIT - Production Database")
    print("="*80)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Query 1: Check Default role users with sensitive modules
    print("\n" + "="*80)
    print(" Query 1: Default Role Users with SENSITIVE Modules (SHOULD BE EMPTY)")
    print("="*80 + "\n")
    
    query1 = """
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
    
    cursor.execute(query1)
    results = cursor.fetchall()
    
    if results:
        print(f"ALERT: Found {len(results)} Default role users with sensitive module access!\n")
        for row in results:
            email, first, last, all_mods, sens_mods = row
            print(f"  Email: {email}")
            print(f"  Name: {first} {last}")
            print(f"  Sensitive Modules: {sens_mods}")
            print(f"  All Modules: {all_mods[:100]}...")
            print("-" * 80)
    else:
        print("OK - No Default role users have sensitive module access\n")
    
    # Query 2: Check specific user (Debasis.Sana@rejlers.ae)
    print("\n" + "="*80)
    print(" Query 2: Specific User Check - Debasis.Sana@rejlers.ae")
    print("="*80 + "\n")
    
    query2 = """
    SELECT 
        u.email,
        u.first_name,
        u.last_name,
        u.is_superuser,
        STRING_AGG(DISTINCT r.code, ', ') as role_codes,
        STRING_AGG(DISTINCT m.code, ', ' ORDER BY m.code) as modules
    FROM auth_user u
    JOIN rbac_userprofile up ON u.id = up.user_id
    JOIN rbac_userrole ur ON up.id = ur.user_profile_id
    JOIN rbac_role r ON ur.role_id = r.id
    LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
    LEFT JOIN rbac_module m ON rm.module_id = m.id
    WHERE 
        u.email = 'Debasis.Sana@rejlers.ae'
        AND up.is_deleted = false
        AND r.is_active = true
    GROUP BY u.id, u.email, u.first_name, u.last_name, u.is_superuser
    """
    
    cursor.execute(query2)
    results = cursor.fetchall()
    
    if results:
        for row in results:
            email, first, last, is_super, roles, mods = row
            print(f"  Email: {email}")
            print(f"  Name: {first} {last}")
            print(f"  Is Superuser: {is_super}")
            print(f"  Roles: {roles}")
            print(f"  Modules: {mods[:200] if mods else 'None'}...")
            
            # Check if has sensitive modules
            if mods:
                sensitive = any(m in str(mods) for m in ['payroll', 'hr_management', 'finance', 'sales', 'procurement'])
                if sensitive:
                    print(f"\n  ALERT: User has access to sensitive modules!")
    else:
        print("User not found in database\n")
    
    # Query 3: All users with Payroll access
    print("\n" + "="*80)
    print(" Query 3: Users with PAYROLL Module Access")
    print("="*80 + "\n")
    
    query3 = """
    SELECT 
        u.email,
        r.code as role_code,
        m.code as module_code
    FROM auth_user u
    JOIN rbac_userprofile up ON u.id = up.user_id
    JOIN rbac_userrole ur ON up.id = ur.user_profile_id
    JOIN rbac_role r ON ur.role_id = r.id
    JOIN rbac_rolemodule rm ON r.id = rm.role_id
    JOIN rbac_module m ON rm.module_id = m.id
    WHERE 
        m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding')
        AND up.is_deleted = false
        AND r.is_active = true
        AND ur.is_primary = true
    ORDER BY m.code, u.email
    """
    
    cursor.execute(query3)
    results = cursor.fetchall()
    
    if results:
        print(f"Found {len(results)} user-module assignments:\n")
        current_module = None
        for row in results:
            email, role, module = row
            if module != current_module:
                if current_module:
                    print()
                print(f"Module: {module}")
                current_module = module
            print(f"  {email} ({role})")
    else:
        print("No users have payroll/HR module access\n")
    
    # Query 4: All users with Finance access
    print("\n" + "="*80)
    print(" Query 4: Users with FINANCE Module Access")
    print("="*80 + "\n")
    
    query4 = """
    SELECT 
        u.email,
        r.code as role_code,
        m.code as module_code
    FROM auth_user u
    JOIN rbac_userprofile up ON u.id = up.user_id
    JOIN rbac_userrole ur ON up.id = ur.user_profile_id
    JOIN rbac_role r ON ur.role_id = r.id
    JOIN rbac_rolemodule rm ON r.id = rm.role_id
    JOIN rbac_module m ON rm.module_id = m.id
    WHERE 
        m.code = 'finance'
        AND up.is_deleted = false
        AND r.is_active = true
        AND ur.is_primary = true
    ORDER BY u.email
    """
    
    cursor.execute(query4)
    results = cursor.fetchall()
    
    if results:
        print(f"Found {len(results)} users:\n")
        for row in results:
            email, role, module = row
            print(f"  {email} ({role})")
    else:
        print("No users have finance module access\n")
    
    # Query 5: Role-Module summary
    print("\n" + "="*80)
    print(" Query 5: System Role-Module Mapping")
    print("="*80 + "\n")
    
    query5 = """
    SELECT 
        r.code as role_code,
        r.level,
        COUNT(DISTINCT m.id) as module_count,
        STRING_AGG(DISTINCT 
            CASE 
                WHEN m.code IN ('payroll', 'hr_management', 'timesheet', 'hr_onboarding', 'finance', 'sales', 'procurement', 'procurement_vendors', 'procurement_orders', 'procurement_requisitions', 'procurement_receipts') 
                THEN m.code 
            END, ', ') as sensitive_modules
    FROM rbac_role r
    LEFT JOIN rbac_rolemodule rm ON r.id = rm.role_id
    LEFT JOIN rbac_module m ON rm.module_id = m.id
    WHERE r.is_active = true
    GROUP BY r.id, r.code, r.level
    ORDER BY r.level, r.code
    """
    
    cursor.execute(query5)
    results = cursor.fetchall()
    
    if results:
        print(f"{'Role':<20} {'Level':<10} {'Total Modules':<15} {'Sensitive Modules':<50}")
        print("-" * 100)
        for row in results:
            role, level, count, sens_mods = row
            print(f"{role:<20} {level:<10} {count or 0:<15} {sens_mods or 'None':<50}")
    
    cursor.close()
    conn.close()
    
    print("\n" + "="*80)
    print(" Audit Complete")
    print("="*80 + "\n")


if __name__ == '__main__':
    try:
        run_audit()
    except Exception as e:
        print(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
