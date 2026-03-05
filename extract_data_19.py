#!/usr/bin/env python
"""Try to extract data for datasheet 19 from S3"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.electrical_datasheet.models import ElectricalDatasheet
from apps.common.services.s3_service import S3Service
import openpyxl
from io import BytesIO

try:
    ds = ElectricalDatasheet.objects.get(id=19)
    print(f'Datasheet ID: {ds.id}')
    print(f'Tag Number: {ds.tag_number}')
    
    # Check if file exists in S3
    file_name = ds.form_data.get('_file_name', '')
    print(f'\nLooking for file: {file_name}')
    
    if not file_name:
        print('ERROR: No file name found in form_data')
        exit(1)
    
    # Try to find the file in S3
    s3_service = S3Service()
    
    # Possible S3 paths
    possible_paths = [
        f'electrical_datasheets/{ds.id}/{file_name}',
        f'datasheets/{ds.id}/{file_name}',
        f'electrical/{ds.id}/{file_name}',
        f'uploads/{file_name}',
        file_name
    ]
    
    file_found = False
    file_content = None
    
    for path in possible_paths:
        try:
            print(f'Trying path: {path}')
            file_content = s3_service.download_file(path)
            if file_content:
                print(f'✓ File found at: {path}')
                file_found = True
                break
        except Exception as e:
            print(f'  Not found: {str(e)[:100]}')
            continue
    
    if not file_found:
        print('\n❌ File not found in S3. Please re-upload the file using the frontend.')
        print('\nSteps to fix:')
        print('1. Go to the datasheet page')
        print('2. Click "Attach File" button')
        print('3. Upload the Excel file')
        print('4. Check "Extract data from file" option')
        print('5. The data will be automatically extracted and populated')
        exit(1)
    
    # Extract data from Excel file
    print(f'\nExtracting data from Excel file...')
    wb = openpyxl.load_workbook(BytesIO(file_content), data_only=True)
    ws = wb.active
    
    extracted_data = {}
    row_count = 0
    
    # Try to extract data (assuming 2-column format: Field | Value)
    for row in ws.iter_rows(min_row=1, values_only=True):
        if row and len(row) >= 2 and row[0]:
            key = str(row[0]).strip()
            value = str(row[1]).strip() if row[1] else ''
            
            if key and value and not key.startswith('_'):
                # Convert to valid field name
                field_name = key.lower().replace(' ', '_').replace('-', '_').replace('/', '_')
                extracted_data[field_name] = value
                row_count += 1
    
    print(f'\n✓ Extracted {row_count} fields from Excel')
    print(f'\nSample extracted data:')
    for key, value in list(extracted_data.items())[:10]:
        print(f'  {key}: {value}')
    
    # Update datasheet
    if extracted_data:
        # Preserve metadata
        ds.form_data.update(extracted_data)
        ds.save()
        print(f'\n✓ Updated datasheet with {len(extracted_data)} fields')
        print(f'Total fields now: {len(ds.form_data)}')
    else:
        print('\n⚠ No data extracted from file')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
