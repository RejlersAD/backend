from apps.designiq.models import EngineeringListItem

items = EngineeringListItem.objects.filter(list_type='line_list')
total = items.count()
with_from_to = items.exclude(data__from_line='').exclude(data__from_line__isnull=True).count()

print(f'Total line items: {total}')
print(f'Items with FROM-TO: {with_from_to}')

sample = items.exclude(data__from_line='').first()
if sample:
    print(f'Sample: {sample.item_tag}')
    print(f'FROM: {sample.data.get(\"from_line\", \"N/A\")}')
    print(f'TO: {sample.data.get(\"to_line\", \"N/A\")}')
