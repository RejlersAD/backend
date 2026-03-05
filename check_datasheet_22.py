#!/usr/bin/env python
"""Check datasheet 22 data and structure"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet
import json

try:
    ds = ElectricalDatasheet.objects.get(id=22)
    print(f'Datasheet ID: {ds.id}')
    print(f'Tag Number: {ds.tag_number}')
    print(f'Equipment Type: {ds.equipment_type}')
    print(f'Equipment Type ID: {ds.equipment_type.id if ds.equipment_type else "None"}')
    print(f'Service Description: {ds.service_description}')
    print(f'\nForm Data Keys ({len(ds.form_data)} total):')
    print(json.dumps(list(ds.form_data.keys()), indent=2))
    
    print(f'\nSample Data (first 20 items):')
    for key, value in list(ds.form_data.items())[:20]:
        print(f'  {key}: {value}')
    
    print(f'\nNon-empty fields count:')
    non_empty = {k: v for k, v in ds.form_data.items() if v}
    print(f'Total non-empty: {len(non_empty)}')
    
    print(f'\nChecking for section-related fields:')
    for key in ds.form_data.keys():
        if any(word in key.lower() for word in ['general', 'battery', 'charger', 'environmental', 'specification']):
            print(f'  {key}: {ds.form_data[key]}')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
