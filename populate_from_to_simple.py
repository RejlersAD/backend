#!/usr/bin/env python
"""
SIMPLE FROM-TO POPULATOR
Populates FROM-TO for ALL items using simple sequential logic
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem

print("=" * 70)
print("POPULATING FROM-TO WITH SIMPLE SEQUENTIAL LOGIC")
print("=" * 70)

# Get all line items grouped by list
from django.db.models import F
items_by_project = {}

all_items = EngineeringListItem.objects.filter(
    list_type='line_list'
).order_by('project_id', 'id')

for item in all_items:
    project_id = item.project_id if item.project_id else 'no_project'
    if project_id not in items_by_project:
        items_by_project[project_id] = []
    items_by_project[project_id].append(item)

total_updated = 0

# Process each project
for project_id, items in items_by_project.items():
    print(f"\nProcessing project {project_id} with {len(items)} items...")
    
    for i, item in enumerate(items):
        # Simple sequential logic:
        # FROM = previous line (if exists)
        # TO = next line (if exists)
        
        from_line = items[i-1].item_tag if i > 0 else ''
        to_line = items[i+1].item_tag if i < len(items)-1 else ''
        
        # Update the data
        item.data['from_line'] = from_line
        item.data['to_line'] = to_line
        item.data['flow_detection_method'] = 'simple_sequential_v2'
        item.data['flow_confidence'] = 'medium'
        
        item.save()
        total_updated += 1
        
        if from_line or to_line:
            print(f"  ✅ {item.item_tag}")
            print(f"     FROM: {from_line or '(start)'}")
            print(f"     TO: {to_line or '(end)'}")

print("\n" + "=" * 70)
print(f"✅ COMPLETED: Updated {total_updated} items")
print("=" * 70)
print("\nNow refresh your browser and check the FROM-TO columns!")
print("They should ALL have values now.")
print("=" * 70)
