#!/usr/bin/env python
"""Check datasheet 19 data"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet
import json

try:
    ds = ElectricalDatasheet.objects.get(id=19)
    print(f'Tag Number: {ds.tag_number}')
    print(f'Equipment Type: {ds.equipment_type}')
    print(f'Service Description: {ds.service_description}')
    print(f'Form Data Type: {type(ds.form_data)}')
    print(f'Form Data Keys: {list(ds.form_data.keys()) if ds.form_data else "None"}')
    print(f'Form Data Length: {len(ds.form_data) if ds.form_data else 0}')
    print(f'\nSample Data (first 10 items):')
    if ds.form_data:
        for key, value in list(ds.form_data.items())[:10]:
            print(f'  {key}: {value}')
    print(f'\nAll non-empty fields:')
    if ds.form_data:
        non_empty = {k: v for k, v in ds.form_data.items() if v}
        print(f'Non-empty count: {len(non_empty)}')
        for key, value in list(non_empty.items())[:20]:
            print(f'  {key}: {value}')
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
