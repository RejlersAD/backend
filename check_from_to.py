import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem

items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')[:5]

print(f"Found {items.count()} recent line list items\n")

for item in items:
    print(f"Tag: {item.item_tag}")
    print(f"  from_line: {item.data.get('from_line', 'NOT SET')}")
    print(f"  to_line: {item.data.get('to_line', 'NOT SET')}")
    print(f"  flow_detection_method: {item.data.get('flow_detection_method', 'NOT SET')}")
    print(f"  All data keys: {list(item.data.keys())}")
    print()
