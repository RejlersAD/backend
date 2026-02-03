#!/usr/bin/env python
"""
Check what data the API is returning vs what's in the database
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem, EngineeringList

print("=" * 60)
print("DATABASE vs API DATA CHECK")
print("=" * 60)

# Check database
all_line_items = EngineeringListItem.objects.filter(list_type='line_list')
print(f"\n✅ Database has {all_line_items.count()} line_list items")

if all_line_items.exists():
    recent = all_line_items.order_by('-created_at').first()
    print(f"\nMost recent item:")
    print(f"  Tag: {recent.item_tag}")
    print(f"  FROM: {recent.data.get('from_line', 'EMPTY')}")
    print(f"  TO: {recent.data.get('to_line', 'EMPTY')}")
    print(f"  List ID: {recent.engineering_list_id}")
    
    # Check the parent list
    parent_list = EngineeringList.objects.filter(id=recent.engineering_list_id).first()
    if parent_list:
        print(f"\nParent List:")
        print(f"  ID: {parent_list.id}")
        print(f"  Name: {parent_list.name}")
        print(f"  Type: {parent_list.list_type}")
    
    # Count items with FROM-TO data
    items_with_from = all_line_items.exclude(data__from_line='').exclude(data__from_line__isnull=True).count()
    items_with_to = all_line_items.exclude(data__to_line='').exclude(data__to_line__isnull=True).count()
    
    print(f"\n📊 FROM-TO Statistics:")
    print(f"  Items with FROM: {items_with_from}/{all_line_items.count()}")
    print(f"  Items with TO: {items_with_to}/{all_line_items.count()}")

# Now check what the API view would return
print(f"\n" + "=" * 60)
print("CHECKING API VIEW")
print("=" * 60)

from apps.designiq.views import EngineeringListViewSet
from rest_framework.test import APIRequestFactory
from django.contrib.auth import get_user_model

User = get_user_model()

# Create a test request
factory = APIRequestFactory()
request = factory.get('/api/v1/designiq/lists/?list_type=line_list')

# Get or create a user for the request
user = User.objects.first()
if not user:
    print("⚠️ No users in database - API might filter out results!")
else:
    request.user = user
    viewset = EngineeringListViewSet()
    viewset.request = request
    queryset = viewset.get_queryset().filter(list_type='line_list')
    
    print(f"\n✅ API queryset has {queryset.count()} items")
    
    if queryset.exists():
        first = queryset.first()
        print(f"\nFirst item in API response:")
        print(f"  Name: {first.name}")
        print(f"  Type: {first.list_type}")
        print(f"  Items count: {first.items.count()}")

print("\n" + "=" * 60)
