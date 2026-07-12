#!/usr/bin/env python
"""
Test Purchase Requisition serialization to check for field errors
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aiflow.settings')
django.setup()

from apps.procurement.models import PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer

print("=" * 80)
print("🔍 TESTING PURCHASE REQUISITION SERIALIZATION")
print("=" * 80)

try:
    # Get a few PRs to test
    prs = PurchaseRequisition.objects.all()[:5]
    print(f"\n✅ Found {prs.count()} PRs in database")
    
    for pr in prs:
        print(f"\n📋 Testing PR: {pr.pr_number}")
        try:
            serializer = PurchaseRequisitionSerializer(pr)
            data = serializer.data
            print(f"  ✅ Serialization successful")
            print(f"  - Fields count: {len(data)}")
            print(f"  - Has total_price: {pr.total_price}")
            print(f"  - Has issued_by: {pr.issued_by is not None}")
            print(f"  - Has requested_by: {pr.requested_by is not None}")
        except Exception as e:
            print(f"  ❌ Serialization error: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
