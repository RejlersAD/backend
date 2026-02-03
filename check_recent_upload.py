#!/usr/bin/env python
"""Check recent P&ID upload FROM-TO data"""

import django
import os
import sys

# Setup Django
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem

# Get most recent items by ID (not timestamp)
recent_items = EngineeringListItem.objects.filter(
    list_type='line_list'
).order_by('-id')[:10]

print(f"\n📊 Most recent P&ID uploads (by ID):")
print(f"Found {recent_items.count()} items\n")

if recent_items.exists():
    for item in recent_items:
        from_line = item.data.get('from_line', '') if item.data else ''
        to_line = item.data.get('to_line', '') if item.data else ''
        
        status = "✅ HAS FROM-TO" if (from_line or to_line) else "❌ EMPTY"
        
        print(f"{status} | ID:{item.id} | {item.item_tag}")
        print(f"  FROM: '{from_line}'")
        print(f"  TO: '{to_line}'")
        print(f"  Created: {item.created_at}")
        
        # Check if this looks like a recent upload
        filename = item.data.get('filename', '') if item.data else ''
        if 'P16093' in filename or 'P16093' in item.item_tag:
            print(f"  📄 FILE: {filename} ⭐ RECENT UPLOAD")
        print()
else:
    print("❌ No items found\n")
    
# Check total
total = EngineeringListItem.objects.filter(list_type='line_list').count()
print(f"\nTotal line list items in database: {total}")
