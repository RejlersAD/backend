"""
Management command to seed initial RBAC data.
Creates default organization, modules, permissions, and roles.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import Organization, Module, Permission, Role, RolePermission, RoleModule, UserProfile

User = get_user_model()


class Command(BaseCommand):
    help = 'Seed initial RBAC data (organizations, modules, permissions, roles)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Starting RBAC seeding...'))

        # Create Default Organization
        org, org_created = Organization.objects.get_or_create(
            name='Rejlers Abu Dhabi',
            defaults={
                'code': 'REJ_UAE',
                'description': 'Default organization for Rejlers Oil & Gas AI Platform',
                's3_bucket_name': 'rejlers-aiflow-storage',
                's3_region': 'eu-west-1',
                'is_active': True,
                'primary_contact_name': 'Admin',
                'primary_contact_email': 'admin@rejlers.ae',
                'city': 'Abu Dhabi',
                'country': 'United Arab Emirates'
            }
        )
        if org_created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created organization: {org.name}'))
        else:
            self.stdout.write(f'  Organization already exists: {org.name}')

        # Define Modules (Features/Apps)
        modules_data = [
            {
                'name': 'PID Analysis',
                'code': 'pid_analysis',
                'description': 'P&ID document analysis and processing',
                'icon': 'FileText',
                'order': 1
            },
            {
                'name': 'PFD to P&ID Converter',
                'code': 'pfd_to_pid',
                'description': 'AI-powered conversion of PFD to P&ID drawings',
                'icon': 'RefreshCw',
                'order': 2
            },
            {
                'name': 'CRS Document Management',
                'code': 'crs_documents',
                'description': 'Upload and manage CRS documents with AI analysis',
                'icon': 'FolderOpen',
                'order': 3
            },
            {
                'name': 'User Management',
                'code': 'user_mgmt',
                'description': 'Manage users, roles, and permissions',
                'icon': 'Users',
                'order': 4
            },
            {
                'name': 'Organization Settings',
                'code': 'org_settings',
                'description': 'Configure organization settings and preferences',
                'icon': 'Settings',
                'order': 5
            },
            {
                'name': 'Audit Logs',
                'code': 'audit_logs',
                'description': 'View system audit logs and activity',
                'icon': 'FileSearch',
                'order': 6
            },
            {
                'name': 'File Storage',
                'code': 'file_storage',
                'description': 'Manage files and documents in S3',
                'icon': 'Database',
                'order': 7
            },
            {
                'name': 'Reports & Analytics',
                'code': 'reports',
                'description': 'Generate reports and view analytics',
                'icon': 'BarChart',
                'order': 8
            },
            {
                'name': 'API Access',
                'code': 'api_access',
                'description': 'Access REST APIs programmatically',
                'icon': 'Code',
                'order': 9
            },
        ]

        modules = {}
        for module_data in modules_data:
            module, created = Module.objects.get_or_create(
                code=module_data['code'],
                defaults=module_data
            )
            modules[module.code] = module
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Created module: {module.name}'))
            else:
                self.stdout.write(f'  Module already exists: {module.name}')

        # Define Permissions
        permissions_data = [
            # PID Analysis Permissions
            {'name': 'Upload P&ID Files', 'code': 'pid_upload', 'module': 'pid_analysis', 'action': 'create', 'description': 'Upload new P&ID files for analysis'},
            {'name': 'View P&ID Analysis', 'code': 'pid_view', 'module': 'pid_analysis', 'action': 'read', 'description': 'View P&ID analysis results'},
            {'name': 'Update P&ID Analysis', 'code': 'pid_update', 'module': 'pid_analysis', 'action': 'update', 'description': 'Update P&ID analysis data'},
            {'name': 'Delete P&ID Analysis', 'code': 'pid_delete', 'module': 'pid_analysis', 'action': 'delete', 'description': 'Delete P&ID analysis records'},
            {'name': 'Approve P&ID Analysis', 'code': 'pid_approve', 'module': 'pid_analysis', 'action': 'approve', 'description': 'Approve P&ID analysis results'},
            {'name': 'Export P&ID Reports', 'code': 'pid_export', 'module': 'pid_analysis', 'action': 'export', 'description': 'Export P&ID reports (PDF/Excel/CSV)'},
            
            # PFD to P&ID Converter Permissions
            {'name': 'Upload PFD Files', 'code': 'pfd_upload', 'module': 'pfd_to_pid', 'action': 'create', 'description': 'Upload PFD files for conversion'},
            {'name': 'View PFD Documents', 'code': 'pfd_view', 'module': 'pfd_to_pid', 'action': 'read', 'description': 'View uploaded PFD documents'},
            {'name': 'Generate P&ID', 'code': 'pfd_convert', 'module': 'pfd_to_pid', 'action': 'execute', 'description': 'Convert PFD to P&ID using AI'},
            {'name': 'View P&ID Conversions', 'code': 'pfd_conversion_view', 'module': 'pfd_to_pid', 'action': 'read', 'description': 'View generated P&ID conversions'},
            {'name': 'Approve P&ID Conversions', 'code': 'pfd_approve', 'module': 'pfd_to_pid', 'action': 'approve', 'description': 'Approve AI-generated P&ID'},
            {'name': 'Delete PFD/Conversions', 'code': 'pfd_delete', 'module': 'pfd_to_pid', 'action': 'delete', 'description': 'Delete PFD documents and conversions'},
            {'name': 'Provide Feedback', 'code': 'pfd_feedback', 'module': 'pfd_to_pid', 'action': 'create', 'description': 'Provide feedback on conversions'},
            
            # CRS Document Management Permissions
            {'name': 'Upload CRS Documents', 'code': 'crs_upload', 'module': 'crs_documents', 'action': 'create', 'description': 'Upload CRS documents for processing'},
            {'name': 'View CRS Documents', 'code': 'crs_view', 'module': 'crs_documents', 'action': 'read', 'description': 'View uploaded CRS documents'},
            {'name': 'Update CRS Documents', 'code': 'crs_update', 'module': 'crs_documents', 'action': 'update', 'description': 'Update CRS document information'},
            {'name': 'Delete CRS Documents', 'code': 'crs_delete', 'module': 'crs_documents', 'action': 'delete', 'description': 'Delete CRS documents'},
            {'name': 'Export CRS Reports', 'code': 'crs_export', 'module': 'crs_documents', 'action': 'export', 'description': 'Export CRS analysis reports'},
            {'name': 'Approve CRS Documents', 'code': 'crs_approve', 'module': 'crs_documents', 'action': 'approve', 'description': 'Approve CRS document analysis'},
            
            # User Management Permissions
            {'name': 'Create Users', 'code': 'user_create', 'module': 'user_mgmt', 'action': 'create', 'description': 'Create new user accounts'},
            {'name': 'View Users', 'code': 'user_view', 'module': 'user_mgmt', 'action': 'read', 'description': 'View user information'},
            {'name': 'Update Users', 'code': 'user_update', 'module': 'user_mgmt', 'action': 'update', 'description': 'Update user information'},
            {'name': 'Delete Users', 'code': 'user_delete', 'module': 'user_mgmt', 'action': 'delete', 'description': 'Delete user accounts'},
            {'name': 'Manage User Roles', 'code': 'user_roles', 'module': 'user_mgmt', 'action': 'update', 'description': 'Assign/revoke user roles'},
            
            # Organization Settings Permissions
            {'name': 'View Organization Settings', 'code': 'org_view', 'module': 'org_settings', 'action': 'read', 'description': 'View organization settings'},
            {'name': 'Update Organization Settings', 'code': 'org_update', 'module': 'org_settings', 'action': 'update', 'description': 'Update organization settings'},
            
            # Audit Log Permissions
            {'name': 'View Audit Logs', 'code': 'audit_view', 'module': 'audit_logs', 'action': 'read', 'description': 'View system audit logs'},
            {'name': 'Export Audit Logs', 'code': 'audit_export', 'module': 'audit_logs', 'action': 'export', 'description': 'Export audit logs'},
            
            # File Storage Permissions
            {'name': 'Upload Files', 'code': 'file_upload', 'module': 'file_storage', 'action': 'create', 'description': 'Upload files to storage'},
            {'name': 'View Files', 'code': 'file_view', 'module': 'file_storage', 'action': 'read', 'description': 'View and download files'},
            {'name': 'Delete Files', 'code': 'file_delete', 'module': 'file_storage', 'action': 'delete', 'description': 'Delete files from storage'},
            
            # Reports & Analytics Permissions
            {'name': 'View Reports', 'code': 'report_view', 'module': 'reports', 'action': 'read', 'description': 'View reports and analytics'},
            {'name': 'Generate Reports', 'code': 'report_generate', 'module': 'reports', 'action': 'execute', 'description': 'Generate custom reports'},
            
            # API Access Permissions
            {'name': 'API Read Access', 'code': 'api_read', 'module': 'api_access', 'action': 'read', 'description': 'Read data via API'},
            {'name': 'API Write Access', 'code': 'api_write', 'module': 'api_access', 'action': 'create', 'description': 'Create/update data via API'},
        ]

        permissions = {}
        for perm_data in permissions_data:
            module_code = perm_data.pop('module')
            perm, created = Permission.objects.get_or_create(
                code=perm_data['code'],
                defaults={**perm_data, 'module': modules[module_code]}
            )
            permissions[perm.code] = perm
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Created permission: {perm.name}'))

        self.stdout.write(f'  Total permissions: {len(permissions)}')

        # Define Roles with Permissions
        roles_data = [
            {
                'name': 'Super Administrator',
                'code': 'super_admin',
                'level': 1,
                'description': 'Full system access - manages all organizations and users',
                'permissions': list(permissions.keys()),  # All permissions
                'modules': list(modules.keys())  # All modules
            },
            {
                'name': 'Administrator',
                'code': 'admin',
                'level': 2,
                'description': 'Organization administrator - manages users and settings',
                'permissions': [
                    'pid_upload', 'pid_view', 'pid_update', 'pid_delete', 'pid_approve', 'pid_export',
                    'pfd_upload', 'pfd_view', 'pfd_convert', 'pfd_conversion_view', 'pfd_approve', 'pfd_delete', 'pfd_feedback',
                    'crs_upload', 'crs_view', 'crs_update', 'crs_delete', 'crs_export', 'crs_approve',
                    'user_create', 'user_view', 'user_update', 'user_delete', 'user_roles',
                    'org_view', 'org_update',
                    'audit_view', 'audit_export',
                    'file_upload', 'file_view', 'file_delete',
                    'report_view', 'report_generate',
                ],
                'modules': ['pid_analysis', 'pfd_to_pid', 'crs_documents', 'user_mgmt', 'org_settings', 'audit_logs', 'file_storage', 'reports']
            },
            {
                'name': 'Manager',
                'code': 'manager',
                'level': 3,
                'description': 'Project manager - can approve and manage documents',
                'permissions': [
                    'pid_upload', 'pid_view', 'pid_update', 'pid_approve', 'pid_export',
                    'pfd_upload', 'pfd_view', 'pfd_convert', 'pfd_conversion_view', 'pfd_approve', 'pfd_feedback',
                    'crs_upload', 'crs_view', 'crs_update', 'crs_export', 'crs_approve',
                    'user_view',
                    'file_upload', 'file_view',
                    'report_view', 'report_generate',
                ],
                'modules': ['pid_analysis', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports']
            },
            {
                'name': 'Engineer',
                'code': 'engineer',
                'level': 4,
                'description': 'Engineering professional - can upload and analyze documents',
                'permissions': [
                    'pid_upload', 'pid_view', 'pid_update', 'pid_export',
                    'pfd_upload', 'pfd_view', 'pfd_convert', 'pfd_conversion_view', 'pfd_feedback',
                    'crs_upload', 'crs_view', 'crs_update', 'crs_export',
                    'file_upload', 'file_view',
                    'report_view',
                ],
                'modules': ['pid_analysis', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports']
            },
            {
                'name': 'Reviewer',
                'code': 'reviewer',
                'level': 5,
                'description': 'Review specialist - can view and export analysis results',
                'permissions': [
                    'pid_view', 'pid_export',
                    'pfd_view', 'pfd_conversion_view', 'pfd_feedback',
                    'crs_view', 'crs_export',
                    'file_view',
                    'report_view',
                ],
                'modules': ['pid_analysis', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports']
            },
            {
                'name': 'Viewer',
                'code': 'viewer',
                'level': 6,
                'description': 'Read-only access - can only view analysis results',
                'permissions': [
                    'pid_view',
                    'pfd_view', 'pfd_conversion_view',
                    'crs_view',
                    'file_view',
                    'report_view',
                ],
                'modules': ['pid_analysis', 'pfd_to_pid', 'crs_documents', 'file_storage', 'reports']
            },
        ]

        for role_data in roles_data:
            perm_codes = role_data.pop('permissions')
            module_codes = role_data.pop('modules')
            
            role, created = Role.objects.get_or_create(
                code=role_data['code'],
                defaults=role_data
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'✓ Created role: {role.name}'))
                
                # Assign permissions
                for perm_code in perm_codes:
                    if perm_code in permissions:
                        RolePermission.objects.get_or_create(
                            role=role,
                            permission=permissions[perm_code]
                        )
                
                # Assign modules
                for module_code in module_codes:
                    if module_code in modules:
                        RoleModule.objects.get_or_create(
                            role=role,
                            module=modules[module_code]
                        )
                
                self.stdout.write(f'  → Assigned {len(perm_codes)} permissions and {len(module_codes)} modules')
            else:
                self.stdout.write(f'  Role already exists: {role.name}')

        # Create UserProfiles for existing users
        users_without_profile = User.objects.filter(rbac_profile__isnull=True)
        for user in users_without_profile:
            profile = UserProfile.objects.create(
                user=user,
                organization=org,
                status='active' if user.is_active else 'inactive'
            )
            self.stdout.write(self.style.SUCCESS(f'✓ Created profile for user: {user.email}'))

        self.stdout.write(self.style.SUCCESS('\n✅ RBAC seeding completed successfully!'))
        self.stdout.write(f'''
Summary:
  - Organization: {org.name}
  - Modules: {len(modules)}
  - Permissions: {len(permissions)}
  - Roles: {len(roles_data)}
  - User Profiles: {UserProfile.objects.count()}
        ''')
