#!/usr/bin/env python
"""Quick check of FROM-TO detection status"""

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

# Check recent items
recent_cutoff = timezone.now() - timedelta(hours=2)
recent_items = EngineeringListItem.objects.filter(
    list_type='line_list',
    created_at__gte=recent_cutoff
).order_by('-created_at')[:10]

if recent_items.exists():
    print(f"\n✅ Found {recent_items.count()} items uploaded in last 2 hours\n")
    
    for item in recent_items:
        from_line = item.data.get('from_line', '')
        to_line = item.data.get('to_line', '')
        method = item.data.get('flow_detection_method', 'NONE')
        
        print(f"Item: {item.item_tag}")
        print(f"  FROM: {from_line or '(empty)'}")
        print(f"  TO: {to_line or '(empty)'}")
        print(f"  Method: {method}")
        print(f"  Created: {item.created_at}")
        print()
else:
    print("\n❌ No items uploaded in last 2 hours")
    
    # Show most recent item
    most_recent = EngineeringListItem.objects.filter(
        list_type='line_list'
    ).order_by('-created_at').first()
    
    if most_recent:
        print("\nMost recent item:")
        print(f"  Item: {most_recent.item_tag}")
        print(f"  Created: {most_recent.created_at}")
        print(f"  FROM: {most_recent.data.get('from_line', '(empty)')}")
        print(f"  TO: {most_recent.data.get('to_line', '(empty)')}")
        print(f"  Method: {most_recent.data.get('flow_detection_method', 'NONE')}")

print("=" * 60)
print("ACTION REQUIRED:")
print("=" * 60)
print("1. Go to: http://localhost:5176/")
print("2. Upload a NEW P&ID document")
print("3. Wait for processing to complete")
print("4. Run this script again to see results")
print("=" * 60)
