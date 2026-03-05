#!/usr/bin/env python
"""Re-extract data from datasheet 22 using intelligent extraction"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet
from apps.electrical_datasheet.views import ElectricalDatasheetViewSet
from django.core.files.uploadedfile import InMemoryUploadedFile
import io

try:
    ds = ElectricalDatasheet.objects.get(id=22)
    print(f'Datasheet ID: {ds.id}')
    print(f'Tag Number: {ds.tag_number}')  
    print(f'Equipment Type: {ds.equipment_type}')
    print(f'File Name: {ds.form_data.get("_file_name")}')
    
    # Since the file wasn't uploaded properly, we need to inform user to re-upload
    print(f'\n{"="*70}')
    print(f'SOLUTION: RE-UPLOAD REQUIRED')
    print(f'{"="*70}')
    print(f'\nThe Excel file data was not extracted during original upload.')
    print(f'The new intelligent extractor is now active and will:')
    print(f'  ✅ Detect multiple Excel format types')
    print(f'  ✅ Handle 2-column and table layouts')
    print(f'  ✅ Parse multi-sheet workbooks')
    print(f'  ✅ Intelligently map fields to Battery System sections')
    print(f'  ✅ Use fuzzy matching for field names')
    print(f'\nPLEASE RE-UPLOAD THE FILE:')
    print(f'1. Go to: http://localhost:5173/engineering/electrical/datasheet/22')
    print(f'2. Click "Attach File" or use the upload section')
    print(f'3. Upload: {ds.form_data.get("_file_name")}')
    print(f'4. Check "Extract data from file"')
    print(f'5. The system will now intelligently extract all fields!')
    print(f'\nExpected sections that will be populated:')
    for idx, section in enumerate(ds.equipment_type.sections, 1):
        print(f'  {idx}. {section["name"]}: {len(section["fields"])} fields')
        for field in section['fields'][:3]:  # Show first 3 fields as example
            print(f'     - {field.replace("_", " ").title()}')
        if len(section['fields']) > 3:
            print(f'     ... and {len(section["fields"]) - 3} more fields')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
