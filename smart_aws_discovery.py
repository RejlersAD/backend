#!/usr/bin/env python
"""
Smart AWS S3 Credential Manager
Soft-coded credential discovery and management system
"""
import os
import sys
from pathlib import Path

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
RESET = '\033[0m'

# Soft-coded credential sources (priority order)
CREDENTIAL_SOURCES = [
    {
        'name': 'Environment Variables',
        'check': lambda: os.environ.get('AWS_ACCESS_KEY_ID'),
        'load': lambda: {
            'access_key': os.environ.get('AWS_ACCESS_KEY_ID', ''),
            'secret_key': os.environ.get('AWS_SECRET_ACCESS_KEY', ''),
            'region': os.environ.get('AWS_S3_REGION_NAME', 'me-central-1'),
            'bucket': os.environ.get('AWS_STORAGE_BUCKET_NAME', ''),
        }
    },
    {
        'name': '.env.local',
        'check': lambda: Path('.env.local').exists(),
        'load': lambda: load_from_file('.env.local')
    },
    {
        'name': '.env',
        'check': lambda: Path('.env').exists(),
        'load': lambda: load_from_file('.env')
    },
    {
        'name': 'backend/.env',
        'check': lambda: Path('backend/.env').exists(),
        'load': lambda: load_from_file('backend/.env')
    },
    {
        'name': 'AWS Credentials File (~/.aws/credentials)',
        'check': lambda: Path.home().joinpath('.aws', 'credentials').exists(),
        'load': lambda: load_from_aws_credentials()
    }
]

def load_from_file(filepath):
    """Load AWS credentials from .env file"""
    creds = {
        'access_key': '',
        'secret_key': '',
        'region': 'me-central-1',
        'bucket': ''
    }
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or not line or '=' not in line:
                    continue
                    
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                
                if key == 'AWS_ACCESS_KEY_ID':
                    creds['access_key'] = value
                elif key == 'AWS_SECRET_ACCESS_KEY':
                    creds['secret_key'] = value
                elif key == 'AWS_S3_REGION_NAME':
                    creds['region'] = value
                elif key == 'AWS_STORAGE_BUCKET_NAME':
                    creds['bucket'] = value
                    
    except Exception as e:
        print(f"{RED}Error reading {filepath}: {e}{RESET}")
        
    return creds

def load_from_aws_credentials():
    """Load from ~/.aws/credentials file"""
    creds = {
        'access_key': '',
        'secret_key': '',
        'region': 'me-central-1',
        'bucket': ''
    }
    
    try:
        aws_creds_path = Path.home().joinpath('.aws', 'credentials')
        with open(aws_creds_path, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if key == 'aws_access_key_id':
                        creds['access_key'] = value
                    elif key == 'aws_secret_access_key':
                        creds['secret_key'] = value
    except Exception as e:
        pass
        
    return creds

def is_valid_credential(value):
    """Check if credential is valid (not placeholder)"""
    if not value:
        return False
    
    placeholders = [
        'your-aws-access-key-here',
        'your-aws-access-key-id',
        'your-aws-secret-key-here',
        '<your-aws-access-key>',
        '<your-aws-secret-key>',
        'AKIAIOSFODNN7EXAMPLE',  # AWS documentation example
    ]
    
    return value not in placeholders and len(value) > 10

def discover_credentials():
    """Smart credential discovery from multiple sources"""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}AWS S3 CREDENTIAL DISCOVERY{RESET}".center(80))
    print(f"{BLUE}{'='*70}{RESET}\n")
    
    print(f"{CYAN}🔍 Searching for credentials in multiple sources...{RESET}\n")
    
    found_sources = []
    final_creds = None
    
    for source in CREDENTIAL_SOURCES:
        print(f"   Checking {source['name']:40} ... ", end='')
        
        if source['check']():
            creds = source['load']()
            
            if is_valid_credential(creds.get('access_key')) and is_valid_credential(creds.get('secret_key')):
                print(f"{GREEN}✓ Found valid credentials{RESET}")
                found_sources.append({
                    'name': source['name'],
                    'creds': creds
                })
                
                if not final_creds:
                    final_creds = creds
                    final_creds['source'] = source['name']
            else:
                print(f"{YELLOW}⚠ Found but invalid/placeholder{RESET}")
        else:
            print(f"{RED}✗ Not found{RESET}")
    
    print(f"\n{BLUE}{'='*70}{RESET}\n")
    
    if final_creds:
        print(f"{GREEN}✓ Using credentials from: {final_creds['source']}{RESET}\n")
        print(f"   Access Key: {final_creds['access_key'][:10]}...{final_creds['access_key'][-4:]}")
        print(f"   Secret Key: {'*' * 20} (hidden)")
        print(f"   Region:     {final_creds['region']}")
        print(f"   Bucket:     {final_creds['bucket']}")
        print()
        
        return final_creds
    else:
        print(f"{RED}✗ No valid AWS credentials found in any source{RESET}\n")
        print(f"{YELLOW}💡 To configure AWS credentials:{RESET}")
        print(f"   1. Get credentials from AWS IAM Console")
        print(f"   2. Update one of these files:")
        for source in CREDENTIAL_SOURCES[:4]:
            print(f"      - {source['name']}")
        print(f"   3. Run this script again\n")
        
        return None

def list_s3_buckets_with_creds(creds):
    """List S3 buckets using discovered credentials"""
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=creds['access_key'],
            aws_secret_access_key=creds['secret_key'],
            region_name=creds['region']
        )
        
        print(f"{CYAN}📋 Listing all S3 buckets:{RESET}\n")
        response = s3_client.list_buckets()
        buckets = response.get('Buckets', [])
        
        if not buckets:
            print(f"   {YELLOW}No buckets found in your AWS account{RESET}")
        else:
            for idx, bucket in enumerate(buckets, 1):
                name = bucket['Name']
                created = bucket['CreationDate'].strftime('%Y-%m-%d %H:%M:%S')
                is_active = f"{GREEN}✓ CONFIGURED{RESET}" if name == creds['bucket'] else ""
                print(f"   {idx}. {name:50} {created} {is_active}")
        
        print(f"\n{GREEN}✅ Found {len(buckets)} bucket(s){RESET}")
        
        # List objects if configured bucket exists
        if creds['bucket'] and any(b['Name'] == creds['bucket'] for b in buckets):
            print(f"\n{CYAN}📦 Contents of '{creds['bucket']}':{RESET}")
            try:
                objects = s3_client.list_objects_v2(
                    Bucket=creds['bucket'],
                    MaxKeys=10
                )
                
                contents = objects.get('Contents', [])
                if contents:
                    print(f"   First 10 objects:")
                    for obj in contents[:10]:
                        size = obj['Size'] / 1024
                        modified = obj['LastModified'].strftime('%Y-%m-%d %H:%M')
                        print(f"   - {obj['Key']:50} {size:>10.2f} KB  {modified}")
                else:
                    print(f"   (Bucket is empty)")
            except ClientError as e:
                print(f"   {RED}Error: {e}{RESET}")
        
        print(f"\n{BLUE}{'='*70}{RESET}\n")
        
    except ImportError:
        print(f"{RED}✗ boto3 not installed. Install with: pip install boto3{RESET}\n")
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_msg = e.response['Error']['Message']
        print(f"{RED}✗ AWS Error ({error_code}): {error_msg}{RESET}\n")
    except Exception as e:
        print(f"{RED}✗ Error: {str(e)}{RESET}\n")

def main():
    """Main execution"""
    creds = discover_credentials()
    
    if creds:
        list_s3_buckets_with_creds(creds)
        return 0
    else:
        return 1

if __name__ == '__main__':
    sys.exit(main())
