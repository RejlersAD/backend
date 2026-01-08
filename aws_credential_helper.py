"""
Secure AWS Credential Helper
============================
Centralized credential management for all AWS operations.
This module ensures credentials are ONLY loaded from environment variables.

SECURITY RULES:
1. NEVER hardcode credentials in any file
2. Always use environment variables via .env file
3. Fail gracefully if credentials are missing
4. Log warnings (not errors) for missing credentials

Usage:
    from aws_credential_helper import get_aws_client, validate_aws_credentials
    
    # Method 1: Direct client
    s3_client = get_aws_client('s3')
    
    # Method 2: Check first
    if validate_aws_credentials():
        s3_client = get_aws_client('s3')
"""

import os
import sys
import boto3
from pathlib import Path
from decouple import config

# Add backend to path for Django settings access
backend_path = Path(__file__).parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))


class AWSCredentialError(Exception):
    """Raised when AWS credentials are missing or invalid"""
    pass


def validate_aws_credentials() -> bool:
    """
    Validate that AWS credentials exist in environment.
    
    Returns:
        bool: True if credentials are present, False otherwise
    """
    access_key = os.environ.get('AWS_ACCESS_KEY_ID') or config('AWS_ACCESS_KEY_ID', default='')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY') or config('AWS_SECRET_ACCESS_KEY', default='')
    
    if not access_key or not secret_key:
        print("⚠️  WARNING: AWS credentials not found in environment variables")
        print("   Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env file")
        return False
    
    if access_key.startswith('your-') or secret_key.startswith('your-'):
        print("⚠️  WARNING: AWS credentials still contain placeholder values")
        print("   Please update .env file with actual AWS credentials")
        return False
    
    return True


def get_aws_credentials() -> dict:
    """
    Safely retrieve AWS credentials from environment.
    
    Returns:
        dict: Credential dictionary with keys: access_key, secret_key, region
        
    Raises:
        AWSCredentialError: If credentials are missing or invalid
    """
    # Try environment variables first
    access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    # Fallback to decouple config
    if not access_key:
        access_key = config('AWS_ACCESS_KEY_ID', default='')
    if not secret_key:
        secret_key = config('AWS_SECRET_ACCESS_KEY', default='')
    
    # Validate
    if not access_key or not secret_key:
        raise AWSCredentialError(
            "AWS credentials not found. Please set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY in your .env file"
        )
    
    if access_key.startswith('your-') or secret_key.startswith('your-'):
        raise AWSCredentialError(
            "AWS credentials contain placeholder values. Please update .env file "
            "with actual AWS credentials from AWS Console"
        )
    
    # Get region (optional, has default)
    region = os.environ.get('AWS_S3_REGION_NAME') or config('AWS_S3_REGION_NAME', default='me-central-1')
    
    return {
        'access_key': access_key,
        'secret_key': secret_key,
        'region': region
    }


def get_aws_client(service_name: str, region: str = None):
    """
    Create AWS service client with credentials from environment.
    
    Args:
        service_name: AWS service name (e.g., 's3', 'dynamodb', 'ec2')
        region: Optional AWS region override
        
    Returns:
        boto3 client instance
        
    Raises:
        AWSCredentialError: If credentials are missing or invalid
        
    Example:
        s3 = get_aws_client('s3')
        buckets = s3.list_buckets()
    """
    creds = get_aws_credentials()
    
    return boto3.client(
        service_name,
        aws_access_key_id=creds['access_key'],
        aws_secret_access_key=creds['secret_key'],
        region_name=region or creds['region']
    )


def get_aws_resource(service_name: str, region: str = None):
    """
    Create AWS service resource with credentials from environment.
    
    Args:
        service_name: AWS service name (e.g., 's3', 'dynamodb')
        region: Optional AWS region override
        
    Returns:
        boto3 resource instance
        
    Example:
        s3 = get_aws_resource('s3')
        bucket = s3.Bucket('my-bucket')
    """
    creds = get_aws_credentials()
    
    return boto3.resource(
        service_name,
        aws_access_key_id=creds['access_key'],
        aws_secret_access_key=creds['secret_key'],
        region_name=region or creds['region']
    )


# Quick validation on import (non-blocking)
if __name__ != "__main__":
    if not validate_aws_credentials():
        print("💡 TIP: Copy backend/.env.example to backend/.env and add your AWS credentials")


if __name__ == "__main__":
    """Test credential loading"""
    print("=" * 70)
    print("AWS Credential Helper - Validation Test")
    print("=" * 70)
    
    if validate_aws_credentials():
        print("✅ AWS credentials found and validated")
        try:
            creds = get_aws_credentials()
            print(f"✅ Access Key: {creds['access_key'][:10]}...{creds['access_key'][-4:]}")
            print(f"✅ Secret Key: {creds['secret_key'][:10]}...{creds['secret_key'][-4:]}")
            print(f"✅ Region: {creds['region']}")
            
            # Test S3 connection
            print("\n🔍 Testing S3 connection...")
            s3 = get_aws_client('s3')
            response = s3.list_buckets()
            print(f"✅ S3 connection successful! Found {len(response['Buckets'])} buckets")
            
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        print("❌ AWS credentials not configured")
        print("\n📝 Setup instructions:")
        print("   1. Copy .env.example to .env")
        print("   2. Add your AWS credentials to .env file")
        print("   3. Run this script again to validate")
