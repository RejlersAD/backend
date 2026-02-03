#!/usr/bin/env python
"""Check upload timestamps"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
from django.utils import timezone
from datetime import timedelta

# Get all line list items
items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')[:20]

print(f"\n📊 Last 20 line list items:\n")
now = timezone.now()

for item in items:
    created = item.created_at
    age = now - created
    
    from_line = item.data.get('from_line', '')
    to_line = item.data.get('to_line', '')
    
    status = "✅ HAS FROM-TO" if (from_line or to_line) else "❌ EMPTY"
    
    print(f"{status} | {item.item_tag}")
    print(f"  Created: {created} ({age.total_seconds() / 3600:.1f} hours ago)")
    if from_line or to_line:
        print(f"  FROM: {from_line or '-'} | TO: {to_line or '-'}")
    print()
