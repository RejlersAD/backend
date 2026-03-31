"""
Management command to seed initial RBAC data.
Creates default organization, modules, permissions, and roles.

Soft-coded: reads ALL_MODULES_CATALOGUE, SYSTEM_ROLES_CONFIG, and ROLE_MODULE_POLICY
from rbac_config.py — edit those dicts to change what gets created, never edit this file.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import Organization, Module, Permission, Role, RolePermission, RoleModule, UserProfile
from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE, SYSTEM_ROLES_CONFIG, ROLE_MODULE_POLICY

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
                's3_bucket_name': 'user-management-rejlers',
                's3_region': 'us-east-1',
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
            {
                'name': 'QHSE Management',
                'code': 'qhse',
                'description': 'Quality, Health, Safety and Environment project management',
                'icon': 'Shield',
                'order': 10
            },
            # ── Engineering Discipline Modules ──────────────────────────
            {
                'name': 'Process Datasheet',
                'code': 'process_datasheet',
                'description': 'Process equipment datasheets — MOV, SDV, pumps, pressure instruments',
                'icon': 'FileText',
                'order': 20
            },
            {
                'name': 'Electrical Datasheet',
                'code': 'electrical_datasheet',
                'description': 'Electrical equipment and SLD-based datasheet generation',
                'icon': 'Zap',
                'order': 21
            },
            {
                'name': 'Electrical SLD',
                'code': 'electrical_sld',
                'description': 'Single Line Diagram analysis and tagging',
                'icon': 'Zap',
                'order': 22
            },
            {
                'name': 'Instrument Datasheet',
                'code': 'instrument_datasheet',
                'description': 'Instrument equipment datasheets and tag lists',
                'icon': 'Activity',
                'order': 23
            },
            {
                'name': 'Instrument Index',
                'code': 'instrument_index',
                'description': 'AI extraction of instrument index from P&ID drawings',
                'icon': 'List',
                'order': 24
            },
            {
                'name': 'Mechanical Datasheet',
                'code': 'mechanical_datasheet',
                'description': 'Mechanical equipment datasheets and inspection records',
                'icon': 'Tool',
                'order': 25
            },
            {
                'name': 'Civil Datasheet',
                'code': 'civil_datasheet',
                'description': 'Civil and structural engineering datasheets',
                'icon': 'Home',
                'order': 26
            },
            {
                'name': 'Piping Datasheet',
                'code': 'piping_datasheet',
                'description': 'Piping material specifications and critical line list',
                'icon': 'GitBranch',
                'order': 27
            },
            {
                'name': 'Piping Material Specification',
                'code': 'piping_pms',
                'description': 'Piping material specification management',
                'icon': 'Database',
                'order': 28
            },
            {
                'name': 'DesignIQ',
                'code': 'designiq',
                'description': 'AI-powered design intelligence and PFD verification',
                'icon': 'Cpu',
                'order': 29
            },
            {
                'name': 'Digitization Datasheet',
                'code': 'digitization_datasheet',
                'description': 'AI-powered digitization of legacy datasheets',
                'icon': 'Scan',
                'order': 30
            },
            {
                'name': 'Spec Customization',
                'code': 'spec_customization',
                'description': 'Engineering specification customization tools',
                'icon': 'Settings',
                'order': 31
            },
        ]

        # ── Merge: add any catalogue entries missing from the hardcoded list ──
        existing_codes = {m['code'] for m in modules_data}
        for cat_entry in ALL_MODULES_CATALOGUE:
            if cat_entry['code'] not in existing_codes:
                modules_data.append(cat_entry)

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

        # ── Create all system roles (soft-coded from SYSTEM_ROLES_CONFIG) ───
        # super_admin and admin get ALL permissions + ALL modules.
        # Discipline roles get modules from ROLE_MODULE_POLICY.
        all_perm_codes = list(permissions.keys())
        all_module_codes = list(modules.keys())

        created_roles = 0
        for role_cfg in SYSTEM_ROLES_CONFIG:
            role_code = role_cfg['code']
            role, created = Role.objects.get_or_create(
                code=role_code,
                defaults={
                    'name': role_cfg['name'],
                    'level': role_cfg['level'],
                    'description': role_cfg['description'],
                    'is_system_role': role_cfg.get('is_system_role', True),
                    'is_active': True,
                }
            )
            if not created:
                # Ensure system flag is set for existing roles
                if not role.is_system_role:
                    role.is_system_role = True
                    role.save(update_fields=['is_system_role'])
                self.stdout.write(f'  Role already exists: {role.name}')
            else:
                created_roles += 1
                self.stdout.write(self.style.SUCCESS(f'✓ Created role: {role.name}'))

            # Assign permissions (super_admin and admin get all)
            if role_code in ('super_admin', 'admin'):
                for perm_code in all_perm_codes:
                    if perm_code in permissions:
                        RolePermission.objects.get_or_create(role=role, permission=permissions[perm_code])

            # Assign modules from ROLE_MODULE_POLICY
            if role_code in ('super_admin', 'admin'):
                role_module_codes = all_module_codes
            else:
                role_module_codes = ROLE_MODULE_POLICY.get(role_code, [])

            assigned = 0
            for module_code in role_module_codes:
                if module_code in modules:
                    _, m_created = RoleModule.objects.get_or_create(role=role, module=modules[module_code])
                    if m_created:
                        assigned += 1

            if assigned:
                self.stdout.write(f'    → Assigned {assigned} new modules to {role.name}')

        self.stdout.write(f'  Created {created_roles} new roles ({len(SYSTEM_ROLES_CONFIG)} total configured)')


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
  - Modules: {Module.objects.count()} in DB
  - Permissions: {len(permissions)}
  - Roles: {Role.objects.count()} in DB ({len(SYSTEM_ROLES_CONFIG)} system roles configured)
  - User Profiles: {UserProfile.objects.count()}
        ''')
