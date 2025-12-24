#!/usr/bin/env python
"""
Create S3 Bucket
Creates the radai-pid-drawings bucket in AWS S3
"""

import boto3
from botocore.exceptions import ClientError
import os

def create_s3_bucket():
    """Create S3 bucket for RADAI"""
    bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'radai-pid-drawings')
    region = os.environ.get('AWS_S3_REGION_NAME', 'ap-south-1')
    
    print(f"Creating S3 bucket: {bucket_name}")
    print(f"Region: {region}")
    
    try:
        s3_client = boto3.client('s3', region_name=region)
        
        # Create bucket with region constraint
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        
        print(f"[SUCCESS] Bucket '{bucket_name}' created successfully!")
        
        # Enable versioning
        s3_client.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={'Status': 'Enabled'}
        )
        print(f"[SUCCESS] Versioning enabled")
        
        # Block public access
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True
            }
        )
        print(f"[SUCCESS] Public access blocked (security best practice)")
        
        return True
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'BucketAlreadyExists':
            print(f"[INFO] Bucket already exists (owned by someone else)")
        elif error_code == 'BucketAlreadyOwnedByYou':
            print(f"[INFO] Bucket already exists (you already own it)")
        else:
            print(f"[FAIL] Error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        return False

if __name__ == "__main__":
    import sys
    success = create_s3_bucket()
    sys.exit(0 if success else 1)
