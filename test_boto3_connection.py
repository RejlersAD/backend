"""
AWS Boto3 Connectivity Test
Tests S3 connectivity, credentials, and bucket access
"""
import boto3
import os
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
from django.core.management.utils import get_random_secret_key


def test_boto3_connectivity():
    """
    Comprehensive boto3 connectivity test
    """
    print("\n" + "="*60)
    print("AWS BOTO3 CONNECTIVITY TEST")
    print("="*60)
    
    # Test 1: Check boto3 installation
    print("\n[1/6] Checking boto3 installation...")
    try:
        print(f"✓ Boto3 version: {boto3.__version__}")
    except Exception as e:
        print(f"✗ Boto3 not installed: {e}")
        return
    
    # Test 2: Check AWS credentials
    print("\n[2/6] Checking AWS credentials...")
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    if not aws_access_key:
        print("✗ AWS_ACCESS_KEY_ID not found in environment")
        return
    else:
        print(f"✓ AWS_ACCESS_KEY_ID: {aws_access_key[:8]}...{aws_access_key[-4:]}")
    
    if not aws_secret_key:
        print("✗ AWS_SECRET_ACCESS_KEY not found in environment")
        return
    else:
        print(f"✓ AWS_SECRET_ACCESS_KEY: {'*' * 20} (configured)")
    
    # Test 3: Test main S3 bucket connection
    print("\n[3/6] Testing main S3 bucket connection...")
    bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers')
    region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
    print(f"   Bucket: {bucket_name}")
    print(f"   Region: {region}")
    
    try:
        s3_client = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        
        # Test bucket access
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"✓ Successfully connected to bucket: {bucket_name}")
        
        # Get bucket location
        location = s3_client.get_bucket_location(Bucket=bucket_name)
        print(f"✓ Bucket location: {location.get('LocationConstraint', 'us-east-1')}")
        
    except NoCredentialsError:
        print("✗ No AWS credentials found")
        return
    except PartialCredentialsError:
        print("✗ Incomplete AWS credentials")
        return
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '403':
            print(f"✗ Access denied to bucket: {bucket_name}")
        elif error_code == '404':
            print(f"✗ Bucket not found: {bucket_name}")
        else:
            print(f"✗ Error: {e}")
        return
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return
    
    # Test 4: Test PFD S3 bucket connection
    print("\n[4/6] Testing PFD S3 bucket connection...")
    pfd_bucket = os.environ.get('PFD_S3_BUCKET', 'rejlers-edrs-project')
    pfd_region = os.environ.get('PFD_S3_REGION', 'ap-south-1')
    print(f"   Bucket: {pfd_bucket}")
    print(f"   Region: {pfd_region}")
    
    try:
        pfd_s3_client = boto3.client(
            's3',
            region_name=pfd_region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        
        pfd_s3_client.head_bucket(Bucket=pfd_bucket)
        print(f"✓ Successfully connected to PFD bucket: {pfd_bucket}")
        
        # List objects in PFD folder
        response = pfd_s3_client.list_objects_v2(
            Bucket=pfd_bucket,
            Prefix='PFD_to_PID/PFD/',
            MaxKeys=5
        )
        
        pfd_count = response.get('KeyCount', 0)
        print(f"✓ Found {pfd_count} PFD files")
        
        # List objects in PID folder
        response = pfd_s3_client.list_objects_v2(
            Bucket=pfd_bucket,
            Prefix='PFD_to_PID/PID/',
            MaxKeys=5
        )
        
        pid_count = response.get('KeyCount', 0)
        print(f"✓ Found {pid_count} P&ID files")
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '403':
            print(f"✗ Access denied to PFD bucket: {pfd_bucket}")
        elif error_code == '404':
            print(f"✗ PFD bucket not found: {pfd_bucket}")
        else:
            print(f"✗ Error: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
    
    # Test 5: List all accessible buckets
    print("\n[5/6] Listing all accessible buckets...")
    try:
        response = s3_client.list_buckets()
        buckets = response.get('Buckets', [])
        print(f"✓ Found {len(buckets)} accessible buckets:")
        for bucket in buckets[:10]:  # Show first 10
            print(f"   - {bucket['Name']} (created: {bucket['CreationDate'].strftime('%Y-%m-%d')})")
        
    except ClientError as e:
        print(f"✗ Failed to list buckets: {e}")
    
    # Test 6: Test upload/download (optional)
    print("\n[6/6] Testing upload/download capabilities...")
    test_key = f"test/connectivity_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    test_content = f"Connectivity test from AIFlow at {datetime.now().isoformat()}"
    
    try:
        # Upload test file
        s3_client.put_object(
            Bucket=bucket_name,
            Key=test_key,
            Body=test_content.encode('utf-8'),
            ContentType='text/plain'
        )
        print(f"✓ Upload test successful: {test_key}")
        
        # Download test file
        response = s3_client.get_object(Bucket=bucket_name, Key=test_key)
        downloaded_content = response['Body'].read().decode('utf-8')
        
        if downloaded_content == test_content:
            print(f"✓ Download test successful")
        else:
            print(f"✗ Download content mismatch")
        
        # Delete test file
        s3_client.delete_object(Bucket=bucket_name, Key=test_key)
        print(f"✓ Cleanup successful")
        
    except ClientError as e:
        print(f"✗ Upload/download test failed: {e}")
    
    # Summary
    print("\n" + "="*60)
    print("CONNECTIVITY TEST COMPLETE")
    print("="*60)
    print("\n✓ Boto3 connectivity is working correctly!")
    print(f"✓ Main bucket: {bucket_name} ({region})")
    print(f"✓ PFD bucket: {pfd_bucket} ({pfd_region})")
    print("\n")


if __name__ == "__main__":
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    
    from datetime import datetime
    test_boto3_connectivity()
