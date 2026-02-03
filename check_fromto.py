#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem
from django.utils import timezone
from datetime import timedelta

cutoff = timezone.now() - timedelta(minutes=5)
recent = EngineeringListItem.objects.filter(
    created_at__gte=cutoff,
    list_type='line_list'
).order_by('-created_at')

print(f'\n🔍 Recent uploads (last 15 min): {recent.count()} items\n')

if recent.count() > 0:
    print('📊 FROM-TO Data Status:\n')
    for item in recent[:10]:
        from_line = item.data.get('from_line', '')
        to_line = item.data.get('to_line', '')
        status = '✅' if (from_line or to_line) else '❌'
        print(f'{status} {item.item_tag}:')
        print(f'   FROM: {from_line if from_line else "(empty)"}')
        print(f'   TO: {to_line if to_line else "(empty)"}')
        print()
else:
    print('❌ NO recent uploads found!')
    print('\nShowing last 3 items from database:')
    all_items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')[:3]
    for item in all_items:
        from_line = item.data.get('from_line', '')
        to_line = item.data.get('to_line', '')
        print(f'\n{item.item_tag} (created: {item.created_at}):')
        print(f'   FROM: {from_line if from_line else "(empty)"}')
        print(f'   TO: {to_line if to_line else "(empty)"}')
