#!/usr/bin/env python
"""Check FROM-TO status in database"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
from django.utils import timezone
from datetime import timedelta

print("=" * 60)
print("FROM-TO STATUS CHECK")
print("=" * 60)

# Get recent items
recent_items = EngineeringListItem.objects.filter(
    list_type='line_list',
    created_at__gte=timezone.now() - timedelta(hours=2)
).order_by('-created_at')[:5]

if recent_items:
    print(f"\n✅ Found {recent_items.count()} items in last 2 hours\n")
    for item in recent_items:
        print(f"Item: {item.item_tag}")
        print(f"  Created: {item.created_at}")
        print(f"  FROM: {item.data.get('from_line', 'NOT SET')}")
        print(f"  TO: {item.data.get('to_line', 'NOT SET')}")
        print(f"  Method: {item.data.get('flow_detection_method', 'NOT SET')}")
        print(f"  Confidence: {item.data.get('flow_confidence', 'NOT SET')}")
        print()
else:
    print("\n❌ No items uploaded in last 2 hours")
    print("\nMost recent item:")
    last = EngineeringListItem.objects.filter(
        list_type='line_list'
    ).order_by('-created_at').first()
    
    if last:
        print(f"  Item: {last.item_tag}")
        print(f"  Created: {last.created_at}")
        print(f"  FROM: {last.data.get('from_line', 'EMPTY')}")
        print(f"  TO: {last.data.get('to_line', 'EMPTY')}")
        print(f"  Method: {last.data.get('flow_detection_method', 'NONE')}")
    else:
        print("  No items found in database!")

print("\n" + "=" * 60)
print("RECOMMENDATION:")
print("=" * 60)
print("1. Upload a P&ID through the frontend")
print("2. Watch logs: docker logs -f aiflow_backend")
print("3. Look for 'PHASE 3A' and 'OpenAI Vision' messages")
print("4. Run this script again to verify data")
print("=" * 60)
