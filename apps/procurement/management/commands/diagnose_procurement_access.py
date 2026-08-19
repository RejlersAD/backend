"""
Django Management Command: Diagnose Procurement Access Issues
Checks why users might not see procurement data on the frontend

DIAGNOSTIC CHECKS:
- User authentication and role
- Module access permissions
- Data availability (POs, PRs, vendors)
- API endpoint accessibility
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor

User = get_user_model()


class Command(BaseCommand):
    help = 'Diagnose procurement access issues for a specific user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='User email to diagnose (if omitted, checks all data)'
        )

    def handle(self, *args, **options):
        email = options.get('email')

        self.stdout.write("=" * 80)
        self.stdout.write("PROCUREMENT ACCESS DIAGNOSTICS")
        self.stdout.write("=" * 80)

        # Check 1: Data availability
        self.stdout.write("\n[1/5] DATABASE - Checking data availability...")
        po_count = PurchaseOrder.objects.count()
        pr_count = PurchaseRequisition.objects.count()
        vendor_count = Vendor.objects.count()

        self.stdout.write(f"  Purchase Orders: {po_count}")
        self.stdout.write(f"  Purchase Requisitions: {pr_count}")
        self.stdout.write(f"  Vendors: {vendor_count}")

        if po_count == 0:
            self.stdout.write(self.style.WARNING("  ⚠️  NO PURCHASE ORDERS FOUND"))
            self.stdout.write("  Action: Run 'python manage.py seed_procurement_data --vendors 5 --prs 5 --pos 5'")
        else:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Data exists"))

        # Check 2: Module configuration
        self.stdout.write("\n[2/5] MODULES - Checking module configuration...")
        procurement_modules = ['procurement', 'procurement_orders', 'procurement_requisitions', 'procurement_vendors']
        
        for module_code in procurement_modules:
            try:
                module = Module.objects.get(code=module_code, is_active=True)
                self.stdout.write(f"  ✓ {module.name} (code={module.code}) - ACTIVE")
            except Module.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"  ✗ '{module_code}' - NOT FOUND"))

        # Check 3: Role configuration
        self.stdout.write("\n[3/5] ROLES - Checking which roles have procurement access...")
        roles_with_procurement = Role.objects.filter(
            modules__code='procurement_orders',
            is_active=True
        ).distinct()

        if roles_with_procurement.exists():
            self.stdout.write("  Roles with 'procurement_orders' access:")
            for role in roles_with_procurement:
                user_count = UserProfile.objects.filter(roles=role).count()
                self.stdout.write(f"    ✓ {role.name} ({role.code}) - {user_count} users")
        else:
            self.stdout.write(self.style.WARNING("  ⚠️  NO ROLES have 'procurement_orders' module"))
            self.stdout.write("  Action: Run 'python manage.py grant_procurement_access'")

        # Check 4: Specific user (if provided)
        if email:
            self.stdout.write(f"\n[4/5] USER - Checking user '{email}'...")
            try:
                user = User.objects.get(email=email)
                self.stdout.write(f"  ✓ User found: {user.email}")
                self.stdout.write(f"    Username: {user.username}")
                self.stdout.write(f"    Superuser: {user.is_superuser}")
                self.stdout.write(f"    Active: {user.is_active}")

                try:
                    profile = user.rbac_profile
                    roles = profile.roles.filter(is_active=True)
                    
                    if roles.exists():
                        self.stdout.write(f"  Roles ({roles.count()}):")
                        for role in roles:
                            self.stdout.write(f"    - {role.name} ({role.code})")
                    else:
                        self.stdout.write(self.style.WARNING("  ⚠️  NO ROLES assigned"))

                    # Check module access
                    has_procurement_orders = profile.has_module_access('procurement_orders')
                    has_procurement_requisitions = profile.has_module_access('procurement_requisitions')
                    
                    self.stdout.write(f"\n  Module Access:")
                    if has_procurement_orders:
                        self.stdout.write(self.style.SUCCESS("    ✓ procurement_orders - GRANTED"))
                    else:
                        self.stdout.write(self.style.ERROR("    ✗ procurement_orders - DENIED"))
                        self.stdout.write("      Action: Grant admin/project_manager role or run grant_procurement_access")
                    
                    if has_procurement_requisitions:
                        self.stdout.write(self.style.SUCCESS("    ✓ procurement_requisitions - GRANTED"))
                    else:
                        self.stdout.write(self.style.ERROR("    ✗ procurement_requisitions - DENIED"))

                except UserProfile.DoesNotExist:
                    self.stdout.write(self.style.ERROR("  ✗ NO RBAC PROFILE found"))
                    self.stdout.write("  Action: User needs to be assigned a role via User Management")

            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"  ✗ User '{email}' not found"))

        else:
            self.stdout.write("\n[4/5] USER - No user specified (use --email to check specific user)")

        # Check 5: Recent purchase orders (show samples)
        self.stdout.write("\n[5/5] SAMPLE DATA - Recent purchase orders...")
        recent_pos = PurchaseOrder.objects.all().select_related('vendor')[:5]
        
        if recent_pos.exists():
            for po in recent_pos:
                vendor_name = po.vendor.name if po.vendor else 'No vendor'
                self.stdout.write(f"  ✓ {po.po_number} | {vendor_name} | {po.status} | ${po.total_amount}")
        else:
            self.stdout.write(self.style.WARNING("  ⚠️  No purchase orders to display"))

        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("DIAGNOSTIC SUMMARY")
        self.stdout.write("=" * 80)
        
        issues = []
        if po_count == 0:
            issues.append("❌ No purchase order data - run seed_procurement_data")
        if not roles_with_procurement.exists():
            issues.append("❌ No roles have procurement_orders access - run grant_procurement_access")
        if email:
            try:
                user = User.objects.get(email=email)
                if not user.rbac_profile.has_module_access('procurement_orders'):
                    issues.append(f"❌ User {email} doesn't have procurement_orders access")
            except:
                pass

        if issues:
            for issue in issues:
                self.stdout.write(issue)
            self.stdout.write("\n🔧 QUICK FIX COMMANDS:")
            self.stdout.write("   python manage.py grant_procurement_access")
            self.stdout.write("   python manage.py seed_procurement_data --vendors 5 --prs 5 --pos 5")
        else:
            self.stdout.write(self.style.SUCCESS("✓ All checks passed!"))
            if email:
                self.stdout.write(f"\nUser '{email}' should be able to see procurement data.")
            self.stdout.write("If frontend still shows no data, check:")
            self.stdout.write("  1. User is logged in with the correct account")
            self.stdout.write("  2. Browser cache/cookies are cleared")
            self.stdout.write("  3. API endpoint returns data: curl https://aiflowbackend-production.up.railway.app/api/v1/procurement/orders/")

        self.stdout.write("=" * 80)
