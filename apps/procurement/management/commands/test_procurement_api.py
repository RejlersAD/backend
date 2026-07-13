"""
Django Management Command: Test Procurement API Endpoint
Simulates what the frontend does when calling /api/v1/procurement/orders/

DEBUGGING CHECKS:
1. Data exists in database
2. User has proper authentication
3. User has RBAC module access
4. API endpoint returns data
5. Serialization works correctly
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.test import force_authenticate
from apps.procurement.models import PurchaseOrder
from apps.procurement.views import PurchaseOrderViewSet
from apps.rbac.models import UserProfile
import json

User = get_user_model()


class Command(BaseCommand):
    help = 'Test procurement API endpoint like frontend does'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='tanzeem.agra@rejlers.ae',
            help='User email to test with (default: tanzeem.agra@rejlers.ae)'
        )

    def handle(self, *args, **options):
        email = options['email']

        self.stdout.write("=" * 80)
        self.stdout.write("TESTING PROCUREMENT API ENDPOINT")
        self.stdout.write("Frontend URL: /procurement/orders")
        self.stdout.write("Backend API: /api/v1/procurement/orders/")
        self.stdout.write("=" * 80)

        # Step 1: Check database
        self.stdout.write("\n[1/6] DATABASE - Checking purchase orders...")
        po_count = PurchaseOrder.objects.count()
        self.stdout.write(f"  Total Purchase Orders: {po_count}")

        if po_count == 0:
            self.stdout.write(self.style.ERROR("  ✗ NO DATA - This is why frontend shows empty!"))
            self.stdout.write("  Fix: python manage.py seed_procurement_data --vendors 5 --prs 5 --pos 5")
            self.stdout.write("=" * 80)
            return
        else:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Data exists ({po_count} orders)"))
            
            # Show sample orders
            sample_orders = PurchaseOrder.objects.all()[:3]
            for po in sample_orders:
                vendor_name = po.vendor.name if po.vendor else 'No vendor'
                self.stdout.write(f"    - {po.po_number} | {vendor_name} | {po.status} | ${po.total_amount}")

        # Step 2: Check user
        self.stdout.write(f"\n[2/6] USER - Checking user '{email}'...")
        try:
            user = User.objects.get(email=email)
            self.stdout.write(self.style.SUCCESS(f"  ✓ User found: {user.email}"))
            self.stdout.write(f"    Superuser: {user.is_superuser}")
            self.stdout.write(f"    Active: {user.is_active}")
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"  ✗ User '{email}' not found"))
            self.stdout.write("=" * 80)
            return

        # Step 3: Check RBAC access
        self.stdout.write(f"\n[3/6] RBAC - Checking module access...")
        try:
            profile = user.rbac_profile
            roles = profile.roles.filter(is_active=True)
            
            self.stdout.write(f"  Roles ({roles.count()}):")
            for role in roles:
                self.stdout.write(f"    - {role.name} ({role.code})")
            
            has_access = user.is_superuser or profile.has_module_access('procurement_orders')
            
            if has_access:
                self.stdout.write(self.style.SUCCESS("  ✓ Has 'procurement_orders' module access"))
            else:
                self.stdout.write(self.style.ERROR("  ✗ MISSING 'procurement_orders' module access"))
                self.stdout.write("  Fix: python manage.py grant_procurement_access")
                self.stdout.write("=" * 80)
                return

        except UserProfile.DoesNotExist:
            if user.is_superuser:
                self.stdout.write(self.style.SUCCESS("  ✓ Is superuser (bypasses RBAC)"))
            else:
                self.stdout.write(self.style.ERROR("  ✗ No RBAC profile and not superuser"))
                self.stdout.write("=" * 80)
                return

        # Step 4: Simulate API request
        self.stdout.write(f"\n[4/6] API REQUEST - Simulating GET /api/v1/procurement/orders/...")
        
        try:
            factory = RequestFactory()
            request = factory.get('/api/v1/procurement/orders/')
            force_authenticate(request, user=user)
            
            view = PurchaseOrderViewSet.as_view({'get': 'list'})
            response = view(request)
            
            self.stdout.write(f"  Status Code: {response.status_code}")
            
            if response.status_code == 200:
                self.stdout.write(self.style.SUCCESS("  ✓ API request successful"))
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ API request failed: {response.status_code}"))
                if hasattr(response, 'data'):
                    self.stdout.write(f"  Error: {response.data}")
                self.stdout.write("=" * 80)
                return

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Exception: {str(e)}"))
            import traceback
            self.stdout.write(traceback.format_exc())
            self.stdout.write("=" * 80)
            return

        # Step 5: Check response data
        self.stdout.write(f"\n[5/6] RESPONSE - Checking API response data...")
        
        try:
            response_data = response.data
            
            # Check if paginated
            if isinstance(response_data, dict) and 'results' in response_data:
                self.stdout.write("  Response Type: Paginated")
                self.stdout.write(f"  Total Count: {response_data.get('count', 'N/A')}")
                orders = response_data.get('results', [])
                self.stdout.write(f"  Orders in Response: {len(orders)}")
                
                if len(orders) == 0:
                    self.stdout.write(self.style.ERROR("  ✗ EMPTY RESULTS - API returns no data"))
                    self.stdout.write("  This is why frontend shows empty!")
                else:
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {len(orders)} orders returned"))
                    
                    # Show first order
                    if orders:
                        first_order = orders[0]
                        self.stdout.write(f"\n  Sample Order:")
                        self.stdout.write(f"    PO Number: {first_order.get('po_number', 'N/A')}")
                        self.stdout.write(f"    Vendor: {first_order.get('vendor_name', 'N/A')}")
                        self.stdout.write(f"    Status: {first_order.get('status', 'N/A')}")
                        self.stdout.write(f"    Total: ${first_order.get('total_amount', 'N/A')}")
                        
            elif isinstance(response_data, list):
                self.stdout.write("  Response Type: List (not paginated)")
                self.stdout.write(f"  Orders in Response: {len(response_data)}")
                
                if len(response_data) == 0:
                    self.stdout.write(self.style.ERROR("  ✗ EMPTY LIST - API returns no data"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"  ✓ {len(response_data)} orders returned"))
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠️  Unexpected response format: {type(response_data)}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Error parsing response: {str(e)}"))

        # Step 6: Frontend compatibility check
        self.stdout.write(f"\n[6/6] FRONTEND - Checking compatibility...")
        
        # Simulate what frontend does
        try:
            data = response.data
            normalizedData = []
            
            if isinstance(data, list):
                normalizedData = data
                self.stdout.write("  Frontend will use: direct array")
            elif data and isinstance(data, dict) and 'results' in data:
                normalizedData = data['results']
                self.stdout.write("  Frontend will use: data.results")
            elif data and isinstance(data, dict):
                normalizedData = [data]
                self.stdout.write("  Frontend will use: wrapped single object")
            
            self.stdout.write(f"  Final array length: {len(normalizedData)}")
            
            if len(normalizedData) > 0:
                self.stdout.write(self.style.SUCCESS(f"  ✓ Frontend will display {len(normalizedData)} orders"))
            else:
                self.stdout.write(self.style.ERROR("  ✗ Frontend will show 'No records' message"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Frontend simulation failed: {str(e)}"))

        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 80)
        
        if len(normalizedData) > 0:
            self.stdout.write(self.style.SUCCESS("✅ ALL CHECKS PASSED"))
            self.stdout.write(f"✅ API returns {len(normalizedData)} orders")
            self.stdout.write(f"✅ User {email} has proper access")
            self.stdout.write(f"✅ Frontend should display data")
            self.stdout.write("\nIf frontend still shows no data:")
            self.stdout.write("  1. User needs to logout and login again (refresh JWT token)")
            self.stdout.write("  2. Clear browser cache (Ctrl+Shift+Delete)")
            self.stdout.write("  3. Check browser console for errors (F12)")
            self.stdout.write("  4. Verify API base URL in frontend .env")
        else:
            self.stdout.write(self.style.ERROR("❌ ISSUE FOUND"))
            self.stdout.write("❌ API returns empty data")
            self.stdout.write("\nPossible causes:")
            self.stdout.write("  1. Database is empty - run seed_procurement_data")
            self.stdout.write("  2. QuerySet is filtered out - check get_queryset() method")
            self.stdout.write("  3. Permissions block data - check RBAC settings")
            
        self.stdout.write("=" * 80)
