"""
Check local procurement data - Purchase Orders and Purchase Requisitions
"""
import os
import django
import sys

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.procurement.models import PurchaseOrder, PurchaseRequisition
from django.contrib.auth import get_user_model

User = get_user_model()

print("=" * 80)
print("LOCAL DATABASE - PROCUREMENT DATA CHECK")
print("=" * 80)

# Check Purchase Requisitions
pr_count = PurchaseRequisition.objects.count()
print(f"\n📋 Purchase Requisitions: {pr_count} records")

if pr_count > 0:
    print("\nRecent PRs:")
    for pr in PurchaseRequisition.objects.all()[:5]:
        print(f"  - PR #{pr.pr_number} | Status: {pr.status} | Created: {pr.created_at.strftime('%Y-%m-%d %H:%M')}")
        print(f"    Requester: {pr.requester.username if pr.requester else 'N/A'}")
        print(f"    Items: {pr.items.count()}")
else:
    print("  ❌ No Purchase Requisitions found in local database")

# Check Purchase Orders
po_count = PurchaseOrder.objects.count()
print(f"\n📦 Purchase Orders: {po_count} records")

if po_count > 0:
    print("\nRecent POs:")
    for po in PurchaseOrder.objects.all()[:5]:
        print(f"  - PO #{po.po_number} | Status: {po.status} | Created: {po.created_at.strftime('%Y-%m-%d %H:%M')}")
        print(f"    Vendor: {po.vendor.name if po.vendor else 'N/A'}")
        print(f"    Total: {po.total_amount}")
else:
    print("  ❌ No Purchase Orders found in local database")

# Check Users
user_count = User.objects.count()
print(f"\n👥 Total Users: {user_count}")

# Check database being used
from django.conf import settings
print(f"\n💾 Database: {settings.DATABASES['default']['ENGINE']}")
if 'sqlite' in settings.DATABASES['default']['ENGINE']:
    print(f"   Database file: {settings.DATABASES['default']['NAME']}")
else:
    print(f"   Database: {settings.DATABASES['default']['NAME']}")
    print(f"   Host: {settings.DATABASES['default'].get('HOST', 'localhost')}")

print("\n" + "=" * 80)
