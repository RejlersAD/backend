#!/usr/bin/env python
"""AWS S3 Configuration and Connectivity Verification"""
import os
import sys

def check_aws_s3():
    print("\n[4/8] AWS S3 Configuration Check...")
    print("=" * 60)
    
    # Check environment variables
    aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
    aws_region = os.getenv('AWS_REGION', 'Not Set')
    s3_bucket = os.getenv('AWS_STORAGE_BUCKET_NAME', 'Not Set')
    
    print(f"AWS Access Key: {'✓ Set (' + aws_access_key[:10] + '...)' if aws_access_key else '✗ Missing'}")
    print(f"AWS Secret Key: {'✓ Set (***hidden***)' if aws_secret_key else '✗ Missing'}")
    print(f"AWS Region: {aws_region}")
    print(f"S3 Bucket: {s3_bucket}")
    
    # Test S3 connectivity
    try:
        import boto3
        from botocore.exceptions import ClientError
        
        s3 = boto3.client(
            's3',
            region_name=aws_region if aws_region != 'Not Set' else 'me-central-1',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        
        # Try to list objects (just 1 to test connectivity)
        response = s3.list_objects_v2(Bucket=s3_bucket, MaxKeys=1)
        
        print(f"\n✓ S3 Connection: SUCCESS")
        print(f"✓ S3 Bucket Accessible: {s3_bucket}")
        
        # Get bucket info
        if 'Contents' in response:
            print(f"✓ Bucket contains objects")
        else:
            print(f"⚠ Bucket is empty or no objects match")
            
    except Exception as e:
        print(f"\n✗ S3 Connection: FAILED")
        print(f"  Error: {str(e)[:200]}")
        return False
    
    return True

if __name__ == '__main__':
    success = check_aws_s3()
    sys.exit(0 if success else 1)
