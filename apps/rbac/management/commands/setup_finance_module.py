"""
Add Finance module to RBAC system
"""
from django.core.management.base import BaseCommand
from apps.rbac.models import Module, Permission, Role, RoleModule, RolePermission
from django.db import transaction


class Command(BaseCommand):
    help = 'Setup Finance Invoice Automation module in RBAC'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(self.style.SUCCESS('FINANCE MODULE SETUP'))
        self.stdout.write(self.style.SUCCESS('='*70))

        with transaction.atomic():
            # Create Finance module
            finance_module, created = Module.objects.get_or_create(
                code='finance',
                defaults={
                    'name': 'Finance Invoice Management',
                    'description': 'AI-powered invoice processing and approval workflow',
                    'icon': 'DollarSign',
                    'order': 6,
                    'is_active': True
                }
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f"\n✅ Created Finance module"))
            else:
                self.stdout.write(self.style.SUCCESS(f"\n✅ Finance module already exists"))
                # Update if needed
                finance_module.icon = 'DollarSign'
                finance_module.order = 6
                finance_module.save()

            # Create Finance permissions
            permissions_data = [
                {'code': 'finance_invoice_view', 'name': 'View Invoices', 'action': 'read', 'description': 'View invoice list and details'},
                {'code': 'finance_invoice_create', 'name': 'Upload Invoices', 'action': 'create', 'description': 'Upload new invoices'},
                {'code': 'finance_invoice_update', 'name': 'Update Invoices', 'action': 'update', 'description': 'Edit invoice information'},
                {'code': 'finance_invoice_delete', 'name': 'Delete Invoices', 'action': 'delete', 'description': 'Delete invoices'},
                {'code': 'finance_invoice_approve', 'name': 'Approve Invoices', 'action': 'approve', 'description': 'Approve/reject invoice payments'},
                {'code': 'finance_invoice_export', 'name': 'Export Invoices', 'action': 'export', 'description': 'Export invoice reports'},
                {'code': 'finance_routes_manage', 'name': 'Manage Approval Routes', 'action': 'update', 'description': 'Configure approval workflows'},
                {'code': 'finance_dashboard', 'name': 'Finance Dashboard', 'action': 'read', 'description': 'View finance analytics and statistics'},
            ]

            permissions_created = 0
            for perm_data in permissions_data:
                permission, created = Permission.objects.get_or_create(
                    code=perm_data['code'],
                    module=finance_module,
                    defaults={
                        'name': perm_data['name'],
                        'action': perm_data['action'],
                        'description': perm_data['description'],
                        'is_active': True
                    }
                )
                if created:
                    permissions_created += 1

            self.stdout.write(self.style.SUCCESS(f"✅ Created {permissions_created} new permissions"))

            # Assign module to admin roles
            admin_roles = Role.objects.filter(level__lte=2, is_active=True)  # Super Admin (1) and Admin (2)
            assigned_count = 0

            for role in admin_roles:
                role_module, created = RoleModule.objects.get_or_create(
                    role=role,
                    module=finance_module
                )
                if created:
                    self.stdout.write(f"  ✅ Assigned to role: {role.name}")
                    assigned_count += 1
                else:
                    self.stdout.write(f"  ℹ️  Already assigned to role: {role.name}")

                # Assign all finance permissions to admin roles
                for perm_data in permissions_data:
                    permission = Permission.objects.get(code=perm_data['code'], module=finance_module)
                    RolePermission.objects.get_or_create(
                        role=role,
                        permission=permission
                    )

            self.stdout.write(self.style.SUCCESS(f"\n✅ Module assigned to {assigned_count} admin role(s)"))

            # Get user count
            from apps.rbac.models import UserProfile
            users_with_access = UserProfile.objects.filter(
                roles__modules=finance_module,
                is_deleted=False,
                status='active'
            ).distinct().count()

            self.stdout.write(self.style.SUCCESS(f"\n📊 SUMMARY:"))
            self.stdout.write(f"  • Module Code: {finance_module.code}")
            self.stdout.write(f"  • Module Name: {finance_module.name}")
            self.stdout.write(f"  • Permissions: {len(permissions_data)}")
            self.stdout.write(f"  • Users with Access: {users_with_access}")
            self.stdout.write(f"  • Order: {finance_module.order}")

            self.stdout.write(self.style.SUCCESS(f"\n✨ Finance module setup completed!"))
            self.stdout.write(self.style.WARNING(
                f"\n💡 To assign Finance access to all CRS users, run:"
            ))
            self.stdout.write(self.style.WARNING(
                f"   python manage.py assign_modules_bulk --modules finance --all-users"
            ))
