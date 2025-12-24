#!/usr/bin/env python
"""
S3 Upload/Download Test
Tests file upload and download to/from S3 bucket
"""

import boto3
from botocore.exceptions import ClientError
import os
from io import BytesIO

def test_s3_operations():
    """Test S3 upload and download operations"""
    bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'radai-pid-drawings')
    region = os.environ.get('AWS_S3_REGION_NAME', 'ap-south-1')
    
    print("="*60)
    print("S3 Upload/Download Test")
    print("="*60)
    
    try:
        s3_client = boto3.client('s3', region_name=region)
        
        # Test 1: Upload a file
        print("\n=== Test 1: Upload File ===")
        test_key = 'test/test_file.txt'
        test_content = b'This is a test file from RADAI application'
        
        s3_client.put_object(
            Bucket=bucket_name,
            Key=test_key,
            Body=test_content,
            ContentType='text/plain'
        )
        print(f"[PASS] Uploaded file: {test_key}")
        
        # Test 2: Download the file
        print("\n=== Test 2: Download File ===")
        response = s3_client.get_object(
            Bucket=bucket_name,
            Key=test_key
        )
        downloaded_content = response['Body'].read()
        
        if downloaded_content == test_content:
            print(f"[PASS] Downloaded file matches uploaded content")
        else:
            print(f"[FAIL] Content mismatch!")
            return False
        
        # Test 3: List objects
        print("\n=== Test 3: List Objects ===")
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix='test/'
        )
        
        if 'Contents' in response:
            print(f"[PASS] Found {response['KeyCount']} object(s) in test/ folder:")
            for obj in response['Contents']:
                print(f"  - {obj['Key']} ({obj['Size']} bytes)")
        else:
            print(f"[FAIL] No objects found")
            return False
        
        # Test 4: Generate presigned URL
        print("\n=== Test 4: Generate Presigned URL ===")
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': test_key},
            ExpiresIn=3600
        )
        print(f"[PASS] Presigned URL generated (expires in 1 hour)")
        print(f"URL: {presigned_url[:80]}...")
        
        # Test 5: Delete test file
        print("\n=== Test 5: Delete File ===")
        s3_client.delete_object(
            Bucket=bucket_name,
            Key=test_key
        )
        print(f"[PASS] Deleted test file: {test_key}")
        
        # Test 6: Upload to different folders
        print("\n=== Test 6: Upload to Multiple Folders ===")
        folders = ['media/pid_drawings/', 'media/pid_reports/', 'media/test/']
        for folder in folders:
            key = f'{folder}test.txt'
            s3_client.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=b'Test file',
                ContentType='text/plain'
            )
            print(f"[PASS] Uploaded to {folder}")
        
        # List all objects
        print("\n=== Current Bucket Contents ===")
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        if 'Contents' in response:
            print(f"Total objects: {response['KeyCount']}")
            for obj in response['Contents']:
                size_kb = obj['Size'] / 1024
                print(f"  - {obj['Key']} ({size_kb:.2f} KB)")
        
        print("\n" + "="*60)
        print("[SUCCESS] All S3 operations test passed!")
        print("="*60)
        return True
        
    except ClientError as e:
        print(f"\n[FAIL] AWS Client Error: {e}")
        return False
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        return False

if __name__ == "__main__":
    import sys
    success = test_s3_operations()
    sys.exit(0 if success else 1)
