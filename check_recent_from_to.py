import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
from django.utils import timezone
from datetime import timedelta

recent = EngineeringListItem.objects.filter(
    list_type='line_list',
    created_at__gte=timezone.now() - timedelta(hours=1)
).order_by('-created_at')[:5]

print(f'Found {recent.count()} recent items in last hour\n')

for item in recent:
    from_line = item.data.get('from_line', 'NOT SET')
    to_line = item.data.get('to_line', 'NOT SET')
    print(f'{item.item_tag}:')
    print(f'  from_line: {from_line}')
    print(f'  to_line: {to_line}')
    print(f'  created: {item.created_at}')
    print()
