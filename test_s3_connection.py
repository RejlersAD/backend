#!/usr/bin/env python
"""
AWS S3 Connection Test
Tests connection to AWS S3 bucket and lists accessible buckets
"""

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import os

def test_s3_connection():
    """Test AWS S3 connection and list buckets"""
    print("="*60)
    print("AWS S3 Connection Test")
    print("="*60)
    
    # Get AWS credentials from environment
    access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    region = os.environ.get('AWS_S3_REGION_NAME', 'ap-south-1')
    bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    use_s3 = os.environ.get('USE_S3', 'False').lower() == 'true'
    
    print(f"\n=== Configuration ===")
    print(f"USE_S3: {use_s3}")
    print(f"AWS Region: {region}")
    print(f"Bucket Name: {bucket_name}")
    print(f"Access Key: {access_key[:10]}...{access_key[-4:] if access_key else 'Not set'}")
    print(f"Secret Key: {'*' * 20 if secret_key else 'Not set'}")
    
    if not access_key or not secret_key:
        print("\n[FAIL] AWS credentials not found in environment!")
        return False
    
    try:
        # Create S3 client
        print(f"\n=== Testing Connection ===")
        s3_client = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key
        )
        
        # List all buckets
        print(f"Listing all buckets...")
        response = s3_client.list_buckets()
        buckets = response.get('Buckets', [])
        
        print(f"\n[PASS] Connected successfully!")
        print(f"Found {len(buckets)} bucket(s):")
        for bucket in buckets:
            print(f"  - {bucket['Name']}")
            
        # Test specific bucket access if configured
        if bucket_name:
            print(f"\n=== Testing Bucket: {bucket_name} ===")
            try:
                # Check if bucket exists and is accessible
                s3_client.head_bucket(Bucket=bucket_name)
                print(f"[PASS] Bucket '{bucket_name}' is accessible")
                
                # Try to list objects (first 10)
                print(f"\nListing objects in bucket (max 10)...")
                objects = s3_client.list_objects_v2(
                    Bucket=bucket_name,
                    MaxKeys=10
                )
                
                if 'Contents' in objects:
                    print(f"Found {objects.get('KeyCount', 0)} objects:")
                    for obj in objects['Contents'][:10]:
                        size_kb = obj['Size'] / 1024
                        print(f"  - {obj['Key']} ({size_kb:.2f} KB)")
                else:
                    print(f"Bucket is empty")
                    
                # Get bucket location
                location = s3_client.get_bucket_location(Bucket=bucket_name)
                bucket_region = location.get('LocationConstraint', 'us-east-1')
                print(f"\nBucket Region: {bucket_region}")
                
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == '404':
                    print(f"[FAIL] Bucket '{bucket_name}' does not exist")
                elif error_code == '403':
                    print(f"[FAIL] Access denied to bucket '{bucket_name}'")
                else:
                    print(f"[FAIL] Error accessing bucket: {e}")
                return False
                
        print("\n" + "="*60)
        print("[SUCCESS] S3 connection test passed!")
        print("="*60)
        return True
        
    except NoCredentialsError:
        print("\n[FAIL] AWS credentials not found or invalid")
        return False
    except ClientError as e:
        print(f"\n[FAIL] AWS Client Error: {e}")
        return False
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        return False

if __name__ == "__main__":
    import sys
    success = test_s3_connection()
    sys.exit(0 if success else 1)
