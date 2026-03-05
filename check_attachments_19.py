#!/usr/bin/env python
"""Check datasheet 19 attachments and extract data"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet, DatasheetAttachment

try:
    ds = ElectricalDatasheet.objects.get(id=19)
    print(f'Datasheet ID: {ds.id}')
    print(f'Tag Number: {ds.tag_number}')
    print(f'Equipment Type: {ds.equipment_type}')
    
    # Check attachments
    attachments = DatasheetAttachment.objects.filter(datasheet=ds)
    print(f'\nAttachments: {attachments.count()}')
    
    for att in attachments:
        print(f'\nAttachment ID: {att.id}')
        print(f'  File: {att.file.name if att.file else "No file"}')
        print(f'  File Type: {att.file_type}')
        print(f'  Uploaded: {att.uploaded_at}')
        print(f'  Description: {att.description}')
    
    print(f'\nCurrent form_data fields: {len(ds.form_data)}')
    print(f'Form data keys: {list(ds.form_data.keys())}')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
