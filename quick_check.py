import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
import json

print("=" * 80)
print("CHECKING DATABASE FOR FROM-TO DATA")
print("=" * 80)

items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')[:5]

print(f"\nTotal items in database: {EngineeringListItem.objects.filter(list_type='line_list').count()}")
print(f"\nShowing first 5 items:\n")

for i, item in enumerate(items, 1):
    print(f"{i}. TAG: {item.item_tag}")
    print(f"   FROM: {item.data.get('from_line', 'NOT SET')}")
    print(f"   TO: {item.data.get('to_line', 'NOT SET')}")
    print(f"   Method: {item.data.get('flow_detection_method', 'NOT SET')}")
    print()

if items:
    print("=" * 80)
    print("FULL JSON STRUCTURE OF FIRST ITEM:")
    print("=" * 80)
    first_item = items[0]
    print(json.dumps({
        'id': str(first_item.id),
        'item_tag': first_item.item_tag,
        'data': first_item.data
    }, indent=2))
