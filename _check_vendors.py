"""Quick Vendor Database Check"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.procurement.models import Vendor

total = Vendor.objects.count()
print(f"\n📊 Total Vendors in Database: {total}")
print(f"\nRecent 5 Vendors:")
for v in Vendor.objects.order_by('-created_at')[:5]:
    print(f"  • {v.vendor_code} - {v.name}")

print(f"\n✅ All {total} vendors accessible!\n")
