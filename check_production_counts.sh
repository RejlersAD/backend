#!/bin/bash
# Quick production database check - to be run in Railway environment

echo "=================================="
echo "PRODUCTION DATABASE CHECK"
echo "=================================="

python manage.py shell <<'PYEOF'
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from django.contrib.auth import get_user_model

User = get_user_model()

pr_count = PurchaseRequisition.objects.count()
po_count = PurchaseOrder.objects.count()
vendor_count = Vendor.objects.count()
user_count = User.objects.count()

print(f"\n=== PRODUCTION DATA COUNTS ===")
print(f"Purchase Requisitions: {pr_count}")
print(f"Purchase Orders: {po_count}")
print(f"Vendors: {vendor_count}")
print(f"Users: {user_count}")

if pr_count == 0 and po_count == 0:
    print(f"\n⚠️  WARNING: NO PROCUREMENT DATA IN PRODUCTION!")
    print(f"   This confirms that local database data does NOT sync to production.")
    print(f"   Data created in local development stays in local database.")
else:
    print(f"\n✅ Procurement data exists in production")

print(f"\n=== CHECKING MIGRATIONS ===")
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app='procurement'")
    mig_count = cursor.fetchone()[0]
    print(f"Procurement migrations applied: {mig_count}")
    
    cursor.execute("SELECT name FROM django_migrations WHERE app='procurement' AND (name LIKE '%0013%' OR name LIKE '%0014%') ORDER BY name")
    new_migs = cursor.fetchall()
    if new_migs:
        print(f"\n✅ Recent enhancement migrations found:")
        for (name,) in new_migs:
            print(f"   - {name}")
    else:
        print(f"\n⚠️  Recent migrations (0013, 0014) NOT FOUND")

print("==================================\n")
PYEOF

echo "Done!"
