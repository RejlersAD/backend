#!/usr/bin/env python3
"""Check most recent upload for FROM-TO data"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
from django.utils import timezone
from datetime import timedelta

# Check items from last 10 minutes
cutoff = timezone.now() - timedelta(minutes=10)
recent = EngineeringListItem.objects.filter(
    list_type='line_list',
    created_at__gte=cutoff
).order_by('-created_at')

print(f"\n{'='*70}")
print(f"ITEMS UPLOADED IN LAST 10 MINUTES: {recent.count()}")
print(f"{'='*70}\n")

if recent.exists():
    for i, item in enumerate(recent[:10], 1):
        print(f"{i}. Line: {item.item_tag}")
        print(f"   Created: {item.created_at}")
        print(f"   FROM: '{item.data.get('from_line', 'MISSING')}'")
        print(f"   TO: '{item.data.get('to_line', 'MISSING')}'")
        print(f"   Method: {item.data.get('flow_detection_method', 'MISSING')}")
        print(f"   Confidence: {item.data.get('flow_confidence', 'MISSING')}")
        print()
else:
    print("❌ NO recent uploads found in last 10 minutes\n")
    print("Checking ALL items...")
    all_items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')[:3]
    for item in all_items:
        print(f"\nMost recent: {item.item_tag}")
        print(f"Created: {item.created_at}")
        print(f"FROM: '{item.data.get('from_line', '')}'")
        print(f"TO: '{item.data.get('to_line', '')}'")

print(f"{'='*70}\n")
