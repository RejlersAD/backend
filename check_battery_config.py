#!/usr/bin/env python
"""Check Battery System equipment type configuration and extract data"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet, ElectricalEquipmentType
import json

try:
    # Check equipment type configuration
    eq_type = ElectricalEquipmentType.objects.get(id='battery')
    print(f'Equipment Type: {eq_type.name}')
    print(f'Code: {eq_type.code}')
    print(f'Sections ({len(eq_type.sections)}):')
    print(json.dumps(eq_type.sections, indent=2))
    
    print(f'\n\n{"="*70}')
    print(f'DATASHEET #22 ANALYSIS')
    print(f'{"="*70}\n')
    
    # Check datasheet
    ds = ElectricalDatasheet.objects.get(id=22)
    print(f'Current form_data fields: {len(ds.form_data)}')
    print(f'File name: {ds.form_data.get("_file_name")}')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
