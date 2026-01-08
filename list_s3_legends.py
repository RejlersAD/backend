"""
List all legend files from AWS S3 bucket
"""
import os
import sys
import django

# Set up Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from django.conf import settings

def list_s3_files(prefix=''):
    """List files in S3 bucket with optional prefix"""
    try:
        # Get AWS credentials from environment
        import os
        aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        region = os.getenv('AWS_S3_REGION_NAME', 'me-central-1')
        bucket_name = os.getenv('AWS_STORAGE_BUCKET_NAME', 'aiflow')
        
        if not aws_access_key or not aws_secret_key:
            print("❌ AWS credentials not found in environment variables")
            print("   Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
            return []
        
        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region
        )
        
        print(f"🔍 Searching S3 bucket: {bucket_name}")
        if prefix:
            print(f"   With prefix: '{prefix}'")
        print()
        
        # List objects
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        
        files = []
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    files.append({
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'modified': obj['LastModified']
                    })
        
        if files:
            print(f"✅ Found {len(files)} files:\n")
            for f in files:
                size_mb = f['size'] / (1024 * 1024)
                print(f"📄 {f['key']}")
                print(f"   Size: {size_mb:.2f} MB | Modified: {f['modified']}")
                print()
        else:
            print(f"⚠️  No files found with prefix '{prefix}'")
            print("\n💡 Try searching with different prefixes:")
            print("   - python list_s3_legends.py legend")
            print("   - python list_s3_legends.py symbols")
            print("   - python list_s3_legends.py pid")
            print("   - python list_s3_legends.py reference")
            print("   - python list_s3_legends.py (list all files)")
        
        return files
        
    except NoCredentialsError:
        print("❌ AWS credentials not found!")
        print("   Check your .env file or environment variables")
        return []
    except ClientError as e:
        print(f"❌ AWS Error: {e}")
        return []
    except Exception as e:
        print(f"❌ Error: {e}")
        return []

if __name__ == '__main__':
    # Get prefix from command line argument
    prefix = sys.argv[1] if len(sys.argv) > 1 else ''
    
    print("="*80)
    print("AWS S3 FILE BROWSER")
    print("="*80)
    print()
    
    files = list_s3_files(prefix)
    
    print()
    print("="*80)
