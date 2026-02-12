#!/usr/bin/env python
"""
List AWS S3 Buckets
"""
import sys
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from decouple import config

def list_s3_buckets():
    """List all S3 buckets"""
    try:
        print("\n" + "="*70)
        print("AWS S3 BUCKET LISTING".center(70))
        print("="*70 + "\n")
        
        # Load credentials directly from environment/config
        # (settings.py may not load them if S3_READY=False)
        access_key = config('AWS_ACCESS_KEY_ID', default='')
        secret_key = config('AWS_SECRET_ACCESS_KEY', default='')
        region = config('AWS_S3_REGION_NAME', default='me-central-1')
        bucket_name = config('AWS_STORAGE_BUCKET_NAME', default='')
        s3_ready = config('S3_READY', default='False')
        use_s3 = config('USE_S3', default='False')
        
        print(f"📌 Configuration Status:")
        print(f"   USE_S3: {use_s3}")
        print(f"   S3_READY: {s3_ready}")
        print(f"   Region: {region}")
        print(f"   Configured Bucket: {bucket_name or '(not set)'}\n")
        
        if not access_key or access_key == 'your-aws-access-key-here':
            print("❌ AWS credentials not configured")
            print("   Update AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in:")
            print("   - .env.local (for local development)")
            print("   - Railway environment variables (for production)\n")
            print("💡 To configure AWS S3:")
            print("   1. Get your credentials from AWS IAM Console")
            print("   2. Update .env.local file")
            print("   3. Set S3_READY=True when credentials are valid")
            print("   4. Restart the container\n")
            return
        
        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        # List all buckets
        print("📋 Listing all S3 buckets in your AWS account:\n")
        response = s3_client.list_buckets()
        
        buckets = response.get('Buckets', [])
        
        if not buckets:
            print("   No buckets found in your AWS account")
        else:
            for idx, bucket in enumerate(buckets, 1):
                name = bucket['Name']
                created = bucket['CreationDate'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Check if this is the configured bucket
                is_active = "✓ CONFIGURED" if name == bucket_name else ""
                
                print(f"   {idx}. {name:50} {created} {is_active}")
        
        print(f"\n✅ Found {len(buckets)} bucket(s) in your AWS account")
        
        # Try to list objects in configured bucket
        if bucket_name and any(b['Name'] == bucket_name for b in buckets):
            print(f"\n📦 Contents of '{bucket_name}':")
            try:
                objects = s3_client.list_objects_v2(
                    Bucket=bucket_name,
                    MaxKeys=10
                )
                
                contents = objects.get('Contents', [])
                if contents:
                    print(f"   First 10 objects:")
                    for obj in contents[:10]:
                        size = obj['Size'] / 1024  # KB
                        modified = obj['LastModified'].strftime('%Y-%m-%d %H:%M')
                        print(f"   - {obj['Key']:50} {size:>10.2f} KB  {modified}")
                else:
                    print(f"   (Bucket is empty)")
                    
            except ClientError as e:
                if e.response['Error']['Code'] == 'AccessDenied':
                    print(f"   ⚠️  Access denied to bucket '{bucket_name}'")
                    print(f"   Check IAM permissions for the AWS credentials")
                else:
                    print(f"   ❌ Error: {e}")
        elif bucket_name:
            print(f"\n⚠️  Configured bucket '{bucket_name}' not found in your account")
            print(f"   Create it or update AWS_STORAGE_BUCKET_NAME in .env.local")
        
        print("\n" + "="*70 + "\n")
        
    except NoCredentialsError:
        print("❌ No AWS credentials found")
        print("   Configure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY\n")
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_msg = e.response['Error']['Message']
        print(f"❌ AWS Error ({error_code}): {error_msg}")
        
        if error_code == 'InvalidAccessKeyId':
            print("\n💡 Your AWS Access Key ID is invalid.")
            print("   Get valid credentials from AWS IAM Console\n")
        elif error_code == 'SignatureDoesNotMatch':
            print("\n💡 Your AWS Secret Access Key is incorrect.")
            print("   Verify your credentials in AWS IAM Console\n")
        else:
            print()
            
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}\n")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    list_s3_buckets()
