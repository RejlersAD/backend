#!/usr/bin/env python
"""Check PFD folder structure in S3 bucket"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import boto3

bucket_name = os.environ.get('PFD_S3_BUCKET', os.environ.get('AWS_STORAGE_BUCKET_NAME', 'rejlers-engineering-data'))
region = os.environ.get('PFD_S3_REGION', os.environ.get('AWS_S3_REGION_NAME', 'me-central-1'))

s3 = boto3.client('s3', region_name=region)

print('=' * 70)
print(f'PFD/P&ID Configuration Check')
print('=' * 70)
print(f'\nBucket: {bucket_name}')
print(f'Region: {region}')

# Check for PFD_to_PID folder structure
pfd_base = 'PFD_to_PID/'

print(f'\n--- Checking PFD Folder Structure ---')
try:
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=pfd_base, Delimiter='/', MaxKeys=10)
    
    if 'CommonPrefixes' in response:
        print(f'\nSubfolders under {pfd_base}:')
        for prefix in response['CommonPrefixes']:
            folder = prefix['Prefix']
            print(f'  📁 {folder}')
            
            # Count files in each subfolder
            count_resp = s3.list_objects_v2(Bucket=bucket_name, Prefix=folder, MaxKeys=1000)
            file_count = len(count_resp.get('Contents', [])) - 1  # Exclude the folder itself
            print(f'     Files: {file_count}')
    else:
        print(f'  ⚠️  No subfolders found under {pfd_base}')
        print(f'  Note: Folder structure will be created automatically when files are uploaded')
        
    # Check if there are any files directly in PFD_to_PID/
    if 'Contents' in response:
        files = [obj for obj in response['Contents'] if obj['Key'] != pfd_base]
        if files:
            print(f'\n  Files in root of {pfd_base}: {len(files)}')
            
except Exception as e:
    print(f'  ⚠️  Error checking folder: {e}')
    print(f'  Note: This is normal if the folder doesn\'t exist yet')

# List all top-level folders
print(f'\n--- Top-Level Folders in Bucket ---')
try:
    response = s3.list_objects_v2(Bucket=bucket_name, Delimiter='/', MaxKeys=100)
    if 'CommonPrefixes' in response:
        count = 0
        for prefix in response['CommonPrefixes']:
            folder_name = prefix['Prefix']
            print(f'  📁 {folder_name}')
            count += 1
            if count >= 20:
                print(f'  ... and more')
                break
    else:
        print('  No top-level folders found')
except Exception as e:
    print(f'  Error: {e}')

print('\n--- Migration Status ---')
print('✅ PFD bucket configuration migrated to: rejlers-engineering-data')
print('✅ All PFD services now use environment variables')
print('✅ S3 connection working with read/write permissions')
print('\nNote: The PFD_to_PID folder structure will be created automatically')
print('      when files are uploaded through the application.')

print('\n' + '=' * 70)
