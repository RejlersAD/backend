#!/usr/bin/env python
"""
Quick script to check if FROM-TO data is in the database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem

# Get most recent line list items
items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')[:10]

print(f"\n📊 Found {items.count()} recent line list items\n")

for item in items:
    from_line = item.data.get('from_line', '')
    to_line = item.data.get('to_line', '')
    method = item.data.get('flow_detection_method', '')
    
    status = "✅ HAS FROM-TO" if (from_line or to_line) else "❌ EMPTY"
    
    print(f"{status} | {item.item_tag}")
    if from_line or to_line:
        print(f"  FROM: {from_line or '-'}")
        print(f"  TO: {to_line or '-'}")
        print(f"  Method: {method}")
    print()
