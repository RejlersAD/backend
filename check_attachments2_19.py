#!/usr/bin/env python
"""Check datasheet 19 attachments and try to extract data"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet
import json

try:
    ds = ElectricalDatasheet.objects.get(id=19)
    print(f'Datasheet ID: {ds.id}')
    print(f'Tag Number: {ds.tag_number}')
    print(f'Equipment Type: {ds.equipment_type}')
    
    # Check attachments
    print(f'\nAttachments (JSON field): {type(ds.attachments)}')
    print(f'Attachments count: {len(ds.attachments) if ds.attachments else 0}')
    
    if ds.attachments:
        for idx, att in enumerate(ds.attachments):
            print(f'\nAttachment {idx + 1}:')
            print(json.dumps(att, indent=2))
    
    print(f'\nCurrent form_data:')
    print(json.dumps(ds.form_data, indent=2))
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
