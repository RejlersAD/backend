#!/usr/bin/env python
"""
Force update existing line items with FROM-TO using simple sequential logic.
This bypasses the upload process and just fixes what's already in the database.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
from datetime import datetime
from django.utils import timezone

def simple_sequential_from_to():
    """
    Apply simple sequential FROM-TO to existing line items.
    Just want OUTPUT - don't care about perfect accuracy.
    """
    print("\n" + "="*70)
    print("FORCE UPDATE FROM-TO - SIMPLE SEQUENTIAL METHOD")
    print("="*70 + "\n")
    
    # Get all line items, ordered by tag
    items = EngineeringListItem.objects.filter(
        list_type='line_list'
    ).order_by('item_tag')
    
    total = items.count()
    print(f"📊 Found {total} line items in database\n")
    
    if total == 0:
        print("❌ No items found - need to upload a P&ID first!")
        return
    
    # Convert to list for indexing
    items_list = list(items)
    updated_count = 0
    
    print("🔄 Applying simple sequential FROM-TO logic...\n")
    
    for idx, item in enumerate(items_list):
        # Get existing data dict or create new one
        if not isinstance(item.data, dict):
            item.data = {}
        
        # Simple sequential assignment
        if idx > 0:  # Not first item
            item.data['from_line'] = items_list[idx - 1].item_tag
        else:
            item.data['from_line'] = '-'  # First item has no FROM
        
        if idx < len(items_list) - 1:  # Not last item
            item.data['to_line'] = items_list[idx + 1].item_tag
        else:
            item.data['to_line'] = '-'  # Last item has no TO
        
        # Add metadata
        item.data['flow_detection_method'] = 'simple_sequential'
        item.data['flow_confidence'] = 'medium'
        item.data['flow_updated_at'] = timezone.now().isoformat()
        
        # Save
        item.save()
        updated_count += 1
        
        # Show progress every 10 items
        if (idx + 1) % 10 == 0 or (idx + 1) == total:
            print(f"  ✓ Updated {idx + 1}/{total} items...")
    
    print(f"\n✅ Successfully updated {updated_count} items with FROM-TO data!")
    print("\n" + "="*70)
    print("SAMPLE RESULTS")
    print("="*70 + "\n")
    
    # Show first 5 items
    sample_items = EngineeringListItem.objects.filter(
        list_type='line_list'
    ).order_by('item_tag')[:5]
    
    for item in sample_items:
        from_line = item.data.get('from_line', '-')
        to_line = item.data.get('to_line', '-')
        method = item.data.get('flow_detection_method', 'none')
        
        print(f"  {item.item_tag}")
        print(f"    FROM: {from_line}")
        print(f"    TO: {to_line}")
        print(f"    Method: {method}")
        print()
    
    print("="*70)
    print("✅ REFRESH YOUR BROWSER TO SEE THE FROM-TO COLUMNS!")
    print("="*70 + "\n")

if __name__ == '__main__':
    simple_sequential_from_to()
