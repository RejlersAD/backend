"""
Django management command to audit RBAC module access in production
Usage: railway run python manage.py audit_rbac_production
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import connection
from apps.rbac.models import Role, Module, UserProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Audit RBAC module access to identify unauthorized users'

    def handle(self, *args, **options):
        self.stdout.write("="*80)
        self.stdout.write(self.style.WARNING(" RBAC SECURITY AUDIT - Production Database"))
        self.stdout.write("="*80 + "\n")

        # Query 1: Default role users with sensitive modules
        self.audit_default_role_users()
        
        # Query 2: Specific user check
        self.audit_specific_user('Debasis.Sana@rejlers.ae')
        
        # Query 3: Payroll access summary
        self.audit_module_access('payroll')
        
        # Query 4: Finance access summary
        self.audit_module_access('finance')
        
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS(" Audit Complete"))
        self.stdout.write("="*80 + "\n")

    def audit_default_role_users(self):
        """Check if any Default role users have sensitive module access"""
        self.stdout.write("\n" + "="*80)
        self.stdout.write(" Query 1: Default Role Users with SENSITIVE Modules")
        self.stdout.write("="*80 + "\n")
        
        query = """
        SELECT 
            u.email,
            u.first_name || ' ' || u.last_name as name,
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
        
        with connection.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
            
            if results:
                self.stdout.write(self.style.ERROR(f"\nALERT: Found {len(results)} Default role users with sensitive access!\n"))
                for row in results:
                    email, name, sens_mods = row
                    self.stdout.write(f"  Email: {email}")
                    self.stdout.write(f"  Name: {name}")
                    self.stdout.write(self.style.ERROR(f"  Sensitive Modules: {sens_mods}"))
                    self.stdout.write("-" * 80)
            else:
                self.stdout.write(self.style.SUCCESS("\nOK - No Default role users have sensitive module access\n"))

    def audit_specific_user(self, email):
        """Check specific user's module access"""
        self.stdout.write("\n" + "="*80)
        self.stdout.write(f" Query 2: User Check - {email}")
        self.stdout.write("="*80 + "\n")
        
        query = """
        SELECT 
            u.email,
            u.first_name || ' ' || u.last_name as name,
            u.is_superuser,
            STRING_AGG(DISTINCT r.code, ', ') as role_codes,
            COUNT(DISTINCT m.id) as module_count,
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
            u.email = %s
            AND up.is_deleted = false
            AND r.is_active = true
        GROUP BY u.id, u.email, u.first_name, u.last_name, u.is_superuser
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [email])
            results = cursor.fetchall()
            
            if results:
                for row in results:
                    user_email, name, is_super, roles, mod_count, sens_mods = row
                    self.stdout.write(f"  Email: {user_email}")
                    self.stdout.write(f"  Name: {name}")
                    self.stdout.write(f"  Is Superuser: {is_super}")
                    self.stdout.write(f"  Roles: {roles}")
                    self.stdout.write(f"  Total Modules: {mod_count}")
                    
                    if sens_mods:
                        self.stdout.write(self.style.ERROR(f"  Sensitive Modules: {sens_mods}"))
                    else:
                        self.stdout.write(self.style.SUCCESS(f"  Sensitive Modules: None"))
            else:
                self.stdout.write(self.style.WARNING(f"User {email} not found\n"))

    def audit_module_access(self, module_code):
        """Check who has access to a specific module"""
        self.stdout.write("\n" + "="*80)
        self.stdout.write(f" Module Access: {module_code.upper()}")
        self.stdout.write("="*80 + "\n")
        
        query = """
        SELECT 
            u.email,
            r.code as role_code
        FROM auth_user u
        JOIN rbac_userprofile up ON u.id = up.user_id
        JOIN rbac_userrole ur ON up.id = ur.user_profile_id
        JOIN rbac_role r ON ur.role_id = r.id
        JOIN rbac_rolemodule rm ON r.id = rm.role_id
        JOIN rbac_module m ON rm.module_id = m.id
        WHERE 
            m.code = %s
            AND up.is_deleted = false
            AND r.is_active = true
            AND ur.is_primary = true
        ORDER BY u.email
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [module_code])
            results = cursor.fetchall()
            
            if results:
                self.stdout.write(f"Found {len(results)} users with {module_code} access:\n")
                for row in results:
                    email, role = row
                    self.stdout.write(f"  {email} ({role})")
            else:
                self.stdout.write(f"No users have {module_code} module access\n")
