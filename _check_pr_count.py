#!/usr/bin/env python
"""Check final PR count"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aiflow.settings')
django.setup()

from apps.procurement.models import PurchaseRequisition

# Count General PRs
gen_prs = PurchaseRequisition.objects.filter(pr_number__startswith='RAD-GEN-PR').order_by('pr_number')
print(f"\n✅ Total General PRs in database: {gen_prs.count()}\n")
print("PR Numbers:")
for pr in gen_prs:
    print(f"  • {pr.pr_number:30} | {pr.status:15} | {pr.currency} {pr.total_price}")

# Count all PRs
all_prs = PurchaseRequisition.objects.all()
print(f"\n📊 Total ALL PRs in database: {all_prs.count()}")
