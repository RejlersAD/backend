#!/usr/bin/env python
"""
Department-Based RBAC Initialization System
Soft-coded approach to align roles with departments and maintain S3 folder structure
Author: AI Assistant
Date: January 26, 2026
"""
import os
import django
import sys
from datetime import datetime

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import (
    Organization, Module, Role, RoleModule, 
    UserProfile, UserRole, Permission, RolePermission
)
import boto3
from botocore.exceptions import ClientError

User = get_user_model()

# ============================================================================
# DEPARTMENT CONFIGURATION (Soft-coded)
# ============================================================================

DEPARTMENT_STRUCTURE = {
    # Delivery Departments
    'Delivery, Instrumentation & Automation': {
        'short_name': 'Instrumentation',
        'folder': 'delivery/instrumentation_automation',
        'modules': ['designiq', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, RIN': {
        'short_name': 'RIN',
        'folder': 'delivery/rin',
        'modules': ['designiq', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, Project Management': {
        'short_name': 'Project Management',
        'folder': 'delivery/project_management',
        'modules': ['user_mgmt', 'org_settings', 'audit_logs', 'reports', 'crs_documents', 'file_storage'],
        'permissions': ['view', 'create', 'edit', 'delete', 'export', 'approve']
    },
    'Delivery, Process Design': {
        'short_name': 'Process Design',
        'folder': 'delivery/process_design',
        'modules': ['designiq', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, PDDS': {
        'short_name': 'PDDS',
        'folder': 'delivery/pdds',
        'modules': ['designiq', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, Civil & Structural': {
        'short_name': 'Civil & Structural',
        'folder': 'delivery/civil_structural',
        'modules': ['designiq', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, Electrical': {
        'short_name': 'Electrical',
        'folder': 'delivery/electrical',
        'modules': ['designiq', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, Project Control': {
        'short_name': 'Project Control',
        'folder': 'delivery/project_control',
        'modules': ['reports', 'audit_logs', 'crs_documents', 'file_storage'],
        'permissions': ['view', 'export']
    },
    'Delivery, Mechanical': {
        'short_name': 'Mechanical',
        'folder': 'delivery/mechanical',
        'modules': ['designiq', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, Layout & Piping': {
        'short_name': 'Layout & Piping',
        'folder': 'delivery/layout_piping',
        'modules': ['designiq', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Delivery, Digital Solutions': {
        'short_name': 'Digital Solutions',
        'folder': 'delivery/digital_solutions',
        'modules': ['user_mgmt', 'org_settings', 'audit_logs', 'designiq', 'pfd_to_pid', 
                    'crs_documents', 'file_storage', 'reports', 'api_access'],
        'permissions': ['view', 'create', 'edit', 'delete', 'export', 'admin']
    },
    'Finance': {
        'short_name': 'Finance',
        'folder': 'finance',
        'modules': ['finance', 'reports', 'audit_logs', 'file_storage'],
        'permissions': ['view', 'create', 'edit', 'approve', 'export']
    },
    'Administration': {
        'short_name': 'Administration',
        'folder': 'administration',
        'modules': ['user_mgmt', 'org_settings', 'audit_logs', 'reports', 'file_storage'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Sales': {
        'short_name': 'Sales',
        'folder': 'sales',
        'modules': ['crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'export']
    },
    'Delivery, Delivery Management': {
        'short_name': 'Delivery Management',
        'folder': 'delivery/delivery_management',
        'modules': ['user_mgmt', 'org_settings', 'audit_logs', 'reports', 'crs_documents', 
                    'file_storage', 'qhse'],
        'permissions': ['view', 'create', 'edit', 'approve', 'export']
    },
    'Delivery, Contracting & Procurement': {
        'short_name': 'Procurement',
        'folder': 'delivery/procurement',
        'modules': ['procurement', 'finance', 'reports', 'file_storage'],
        'permissions': ['view', 'create', 'edit', 'approve', 'export']
    },
    'Delivery, QHSE': {
        'short_name': 'QHSE',
        'folder': 'delivery/qhse',
        'modules': ['qhse', 'audit_logs', 'reports', 'file_storage'],
        'permissions': ['view', 'create', 'edit', 'approve', 'export']
    },
    'Delivery, QA/QC': {
        'short_name': 'QA/QC',
        'folder': 'delivery/qaqc',
        'modules': ['qhse', 'audit_logs', 'reports', 'crs_documents', 'file_storage'],
        'permissions': ['view', 'create', 'edit', 'export']
    },
    'Abu Dhabi': {
        'short_name': 'Abu Dhabi Office',
        'folder': 'offices/abu_dhabi',
        'modules': ['crs_documents', 'file_storage', 'reports'],
        'permissions': ['view', 'create', 'export']
    },
    'Management': {
        'short_name': 'Management',
        'folder': 'management',
        'modules': ['user_mgmt', 'org_settings', 'audit_logs', 'reports', 'crs_documents', 
                    'file_storage', 'qhse', 'finance', 'procurement'],
        'permissions': ['view', 'create', 'edit', 'delete', 'approve', 'export', 'admin']
    }
}

# Super Admin Configuration
SUPER_ADMIN_EMAIL = 'tanzeem.agra@rejlers.ae'

# S3 Configuration
S3_BUCKET = os.getenv('AWS_STORAGE_BUCKET_NAME', 'rejlers-engineering-data')
S3_REGION = os.getenv('AWS_S3_REGION_NAME', 'me-central-1')


# ============================================================================
# S3 FOLDER STRUCTURE INITIALIZATION
# ============================================================================

def initialize_s3_folder_structure():
    """Create department-based folder structure in S3"""
    print("\n" + "="*80)
    print("📁 INITIALIZING S3 DEPARTMENT FOLDER STRUCTURE")
    print("="*80)
    
    try:
        s3_client = boto3.client('s3', region_name=S3_REGION)
        
        # Test bucket access
        try:
            s3_client.head_bucket(Bucket=S3_BUCKET)
            print(f"✅ Connected to S3 bucket: {S3_BUCKET}")
        except ClientError as e:
            print(f"❌ Cannot access S3 bucket: {e}")
            return False
        
        created_folders = []
        
        for dept_name, config in DEPARTMENT_STRUCTURE.items():
            folder_path = config['folder']
            
            # Create main folder
            main_folder_key = f"departments/{folder_path}/"
            
            # Create subfolders
            subfolders = [
                f"{main_folder_key}documents/",
                f"{main_folder_key}drawings/",
                f"{main_folder_key}reports/",
                f"{main_folder_key}confidential/",
                f"{main_folder_key}archive/",
                f"{main_folder_key}history/{datetime.now().year}/"
            ]
            
            for subfolder in subfolders:
                try:
                    s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=subfolder,
                        Body=b'',
                        ServerSideEncryption='AES256',
                        Metadata={
                            'department': dept_name,
                            'created_by': 'system',
                            'created_at': datetime.now().isoformat()
                        }
                    )
                    created_folders.append(subfolder)
                except ClientError as e:
                    print(f"  ⚠️  Failed to create {subfolder}: {e}")
        
        print(f"\n✅ Created {len(created_folders)} S3 folders")
        print(f"   Structure: departments/<department_folder>/<subfolder>/")
        return True
        
    except Exception as e:
        print(f"❌ S3 initialization failed: {e}")
        return False


# ============================================================================
# DEPARTMENT ROLE CREATION
# ============================================================================

def create_department_roles():
    """Create roles for each department"""
    print("\n" + "="*80)
    print("🎭 CREATING DEPARTMENT-BASED ROLES")
    print("="*80)
    
    roles_created = []
    roles_updated = []
    
    for dept_name, config in DEPARTMENT_STRUCTURE.items():
        short_name = config['short_name']
        role_code = f"dept_{short_name.lower().replace(' ', '_').replace('&', 'and')}"
        role_name = f"{short_name} Department"
        
        # Determine role level based on department type
        if 'Management' in dept_name or 'Digital Solutions' in dept_name:
            level = 20  # Management level
        elif dept_name in ['Finance', 'Administration', 'Delivery, Delivery Management']:
            level = 30  # Senior level
        else:
            level = 40  # Standard delivery level
        
        role, created = Role.objects.get_or_create(
            code=role_code,
            defaults={
                'name': role_name,
                'description': f"Role for {dept_name} department with appropriate module access",
                'level': level,
                'is_active': True
            }
        )
        
        if created:
            roles_created.append(role_name)
            print(f"  ✅ Created: {role_name} (Level {level})")
        else:
            roles_updated.append(role_name)
            print(f"  ℹ️  Exists: {role_name}")
        
        # Assign modules to role
        module_codes = config['modules']
        modules = Module.objects.filter(code__in=module_codes, is_active=True)
        
        for module in modules:
            RoleModule.objects.get_or_create(
                role=role,
                module=module
            )
        
        print(f"     Assigned {modules.count()} modules: {', '.join(module_codes)}")
    
    print(f"\n📊 Summary:")
    print(f"   • Created: {len(roles_created)} roles")
    print(f"   • Updated: {len(roles_updated)} roles")
    print(f"   • Total: {len(DEPARTMENT_STRUCTURE)} department roles")
    
    return True


# ============================================================================
# ASSIGN USERS TO DEPARTMENT ROLES
# ============================================================================

def assign_users_to_department_roles():
    """Assign users to roles based on their department"""
    print("\n" + "="*80)
    print("👥 ASSIGNING USERS TO DEPARTMENT ROLES")
    print("="*80)
    
    stats = {
        'total_users': 0,
        'assigned': 0,
        'already_assigned': 0,
        'no_department': 0,
        'dept_not_found': 0,
        'errors': 0
    }
    
    # Get all active users
    users = User.objects.filter(is_active=True)
    stats['total_users'] = users.count()
    
    print(f"Processing {stats['total_users']} users...")
    
    for user in users:
        try:
            # Get or create user profile
            profile, _ = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'organization': Organization.objects.first(),
                    'status': 'active'
                }
            )
            
            # Check if user has department
            if not profile.department:
                stats['no_department'] += 1
                continue
            
            dept_name = profile.department
            
            # Find department configuration
            if dept_name not in DEPARTMENT_STRUCTURE:
                stats['dept_not_found'] += 1
                print(f"  ⚠️  Department not found for {user.email}: {dept_name}")
                continue
            
            config = DEPARTMENT_STRUCTURE[dept_name]
            short_name = config['short_name']
            role_code = f"dept_{short_name.lower().replace(' ', '_').replace('&', 'and')}"
            
            # Get role
            role = Role.objects.filter(code=role_code).first()
            if not role:
                stats['errors'] += 1
                print(f"  ❌ Role not found: {role_code}")
                continue
            
            # Check if already assigned
            existing = UserRole.objects.filter(
                user_profile=profile,
                role=role
            ).exists()
            
            if existing:
                stats['already_assigned'] += 1
                continue
            
            # Assign role
            # Check if user has any primary role
            has_primary = UserRole.objects.filter(
                user_profile=profile,
                is_primary=True
            ).exists()
            
            UserRole.objects.create(
                user_profile=profile,
                role=role,
                is_primary=not has_primary
            )
            
            stats['assigned'] += 1
            print(f"  ✅ {user.email} → {role.name}")
            
        except Exception as e:
            stats['errors'] += 1
            print(f"  ❌ Error processing {user.email}: {e}")
    
    print(f"\n📊 Assignment Summary:")
    print(f"   • Total Users: {stats['total_users']}")
    print(f"   • Newly Assigned: {stats['assigned']}")
    print(f"   • Already Assigned: {stats['already_assigned']}")
    print(f"   • No Department: {stats['no_department']}")
    print(f"   • Department Not Found: {stats['dept_not_found']}")
    print(f"   • Errors: {stats['errors']}")
    
    return True


# ============================================================================
# CONFIGURE SUPER ADMIN
# ============================================================================

def configure_super_admin():
    """Ensure tanzeem.agra@rejlers.ae has super admin privileges"""
    print("\n" + "="*80)
    print("👑 CONFIGURING SUPER ADMINISTRATOR")
    print("="*80)
    
    try:
        user = User.objects.filter(email=SUPER_ADMIN_EMAIL).first()
        
        if not user:
            print(f"❌ Super admin user not found: {SUPER_ADMIN_EMAIL}")
            return False
        
        print(f"✅ Found user: {user.email}")
        
        # Update user flags
        user.is_superuser = True
        user.is_staff = True
        user.save()
        print(f"  ✅ Set is_superuser=True, is_staff=True")
        
        # Get or create profile
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'organization': Organization.objects.first(),
                'status': 'active',
                'department': 'Delivery, Digital Solutions',
                'job_title': 'Digital Transformation Lead'
            }
        )
        
        # Get super_admin role
        super_admin_role = Role.objects.filter(code='super_admin').first()
        
        if not super_admin_role:
            print("  ⚠️  Creating super_admin role...")
            super_admin_role = Role.objects.create(
                name='Super Administrator',
                code='super_admin',
                description='Full system access with all privileges',
                level=1,
                is_active=True
            )
            
            # Assign all modules
            all_modules = Module.objects.filter(is_active=True)
            for module in all_modules:
                RoleModule.objects.get_or_create(
                    role=super_admin_role,
                    module=module
                )
            
            print(f"  ✅ Created super_admin role with {all_modules.count()} modules")
        
        # Assign super_admin role
        user_role, created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=super_admin_role,
            defaults={'is_primary': True}
        )
        
        if created:
            print(f"  ✅ Assigned super_admin role")
        else:
            print(f"  ℹ️  Already has super_admin role")
        
        # Verify modules
        modules = profile.get_all_modules()
        print(f"  ✅ Super admin has access to {modules.count()} modules")
        
        return True
        
    except Exception as e:
        print(f"❌ Error configuring super admin: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Execute department RBAC initialization"""
    print("\n" + "="*80)
    print("🚀 DEPARTMENT-BASED RBAC INITIALIZATION")
    print("="*80)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Departments: {len(DEPARTMENT_STRUCTURE)}")
    print(f"Super Admin: {SUPER_ADMIN_EMAIL}")
    print(f"S3 Bucket: {S3_BUCKET}")
    
    results = []
    
    # Step 1: Initialize S3 folders
    print("\n[Step 1/4] Initializing S3 folder structure...")
    s3_success = initialize_s3_folder_structure()
    results.append(('S3 Folders', s3_success))
    
    # Step 2: Create department roles
    print("\n[Step 2/4] Creating department roles...")
    roles_success = create_department_roles()
    results.append(('Department Roles', roles_success))
    
    # Step 3: Assign users to roles
    print("\n[Step 3/4] Assigning users to department roles...")
    assignment_success = assign_users_to_department_roles()
    results.append(('User Assignments', assignment_success))
    
    # Step 4: Configure super admin
    print("\n[Step 4/4] Configuring super administrator...")
    admin_success = configure_super_admin()
    results.append(('Super Admin', admin_success))
    
    # Final summary
    print("\n" + "="*80)
    print("📊 INITIALIZATION SUMMARY")
    print("="*80)
    
    for task, success in results:
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"  {status} - {task}")
    
    all_success = all(result[1] for result in results)
    
    if all_success:
        print("\n" + "="*80)
        print("✅ DEPARTMENT RBAC INITIALIZATION COMPLETE!")
        print("="*80)
        print("\n📋 Next Steps:")
        print("   1. Verify user access at https://www.radai.ae/admin/users")
        print("   2. Check S3 folder structure in AWS console")
        print("   3. Test department-based data isolation")
        print("   4. Review audit logs for all changes")
        print("\n💡 Department folders created at:")
        print(f"   s3://{S3_BUCKET}/departments/<department_folder>/")
    else:
        print("\n⚠️  Some steps failed. Please review errors above.")
    
    print("")


if __name__ == '__main__':
    main()
