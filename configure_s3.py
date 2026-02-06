#!/usr/bin/env python
"""
AWS S3 Configuration & Validation Script
=========================================
Purpose: Help configure and validate AWS S3 credentials for AIFlow
Usage: python configure_s3.py
"""

import os
import sys
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from decouple import config

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text):
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}{text.center(70)}{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")

def print_success(text):
    print(f"{GREEN}✓ {text}{RESET}")

def print_error(text):
    print(f"{RED}✗ {text}{RESET}")

def print_warning(text):
    print(f"{YELLOW}⚠ {text}{RESET}")

def print_info(text):
    print(f"{BLUE}ℹ {text}{RESET}")

def check_env_file():
    """Check if .env.local exists"""
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env.local')
    if not os.path.exists(env_path):
        print_error(f".env.local not found at: {env_path}")
        print_info("Create .env.local by copying from .env.example")
        return False
    return True

def get_s3_config():
    """Load S3 configuration from environment"""
    return {
        'USE_S3': config('USE_S3', default='False'),
        'S3_READY': config('S3_READY', default='False'),
        'AWS_ACCESS_KEY_ID': config('AWS_ACCESS_KEY_ID', default=''),
        'AWS_SECRET_ACCESS_KEY': config('AWS_SECRET_ACCESS_KEY', default=''),
        'AWS_STORAGE_BUCKET_NAME': config('AWS_STORAGE_BUCKET_NAME', default=''),
        'AWS_S3_REGION_NAME': config('AWS_S3_REGION_NAME', default='us-east-1'),
    }

def validate_credentials(access_key, secret_key):
    """Check if credentials are valid (not placeholders)"""
    placeholders = ['your-aws', 'placeholder', 'change-me', 'xxx', 'yyy']
    
    if not access_key or not secret_key:
        return False, "Credentials are empty"
    
    if any(ph in access_key.lower() for ph in placeholders):
        return False, "Access Key is a placeholder"
    
    if any(ph in secret_key.lower() for ph in placeholders):
        return False, "Secret Key is a placeholder"
    
    if len(access_key) < 16:
        return False, "Access Key too short (should be ~20 characters)"
    
    if len(secret_key) < 30:
        return False, "Secret Key too short (should be ~40 characters)"
    
    return True, "Credentials format looks valid"

def test_s3_connection(access_key, secret_key, bucket_name, region):
    """Test actual connection to S3"""
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        # Test 1: List buckets (verifies credentials work)
        print_info("Testing S3 credentials...")
        response = s3_client.list_buckets()
        print_success(f"Connected to AWS S3 successfully")
        print_info(f"Found {len(response['Buckets'])} accessible bucket(s)")
        
        # Test 2: Check if specific bucket exists
        print_info(f"Checking bucket: {bucket_name}")
        s3_client.head_bucket(Bucket=bucket_name)
        print_success(f"Bucket '{bucket_name}' exists and is accessible")
        
        # Test 3: Check bucket location
        location = s3_client.get_bucket_location(Bucket=bucket_name)
        bucket_region = location['LocationConstraint'] or 'us-east-1'
        print_info(f"Bucket region: {bucket_region}")
        
        if bucket_region != region:
            print_warning(f"Region mismatch: configured={region}, actual={bucket_region}")
            print_warning(f"Update AWS_S3_REGION_NAME={bucket_region} in .env.local")
        
        return True
        
    except NoCredentialsError:
        print_error("No AWS credentials found")
        return False
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '403':
            print_error("Access Denied - Credentials are invalid or lack permissions")
        elif error_code == '404':
            print_error(f"Bucket '{bucket_name}' does not exist or is not accessible")
        else:
            print_error(f"AWS Error: {error_code} - {e.response['Error']['Message']}")
        return False
    except Exception as e:
        print_error(f"Connection test failed: {str(e)}")
        return False

def main():
    print_header("AWS S3 Configuration Validator for AIFlow")
    
    # Step 1: Check environment file
    print_info("Step 1: Checking environment configuration...")
    if not check_env_file():
        sys.exit(1)
    
    # Step 2: Load configuration
    print_info("Step 2: Loading S3 configuration from .env.local...")
    config_data = get_s3_config()
    
    print(f"\n  USE_S3: {config_data['USE_S3']}")
    print(f"  S3_READY: {config_data['S3_READY']}")
    print(f"  Bucket: {config_data['AWS_STORAGE_BUCKET_NAME']}")
    print(f"  Region: {config_data['AWS_S3_REGION_NAME']}")
    print(f"  Access Key: {'*' * 12}{config_data['AWS_ACCESS_KEY_ID'][-4:] if len(config_data['AWS_ACCESS_KEY_ID']) > 4 else '(not set)'}")
    
    # Step 3: Validate credential format
    print_info("\nStep 3: Validating credential format...")
    is_valid, message = validate_credentials(
        config_data['AWS_ACCESS_KEY_ID'],
        config_data['AWS_SECRET_ACCESS_KEY']
    )
    
    if not is_valid:
        print_error(message)
        print_warning("\nTo fix this:")
        print("  1. Go to AWS IAM Console: https://console.aws.amazon.com/iam/")
        print("  2. Navigate to: Security Credentials → Access Keys")
        print("  3. Create new access key if needed")
        print("  4. Update .env.local with:")
        print("     AWS_ACCESS_KEY_ID=AKIA...")
        print("     AWS_SECRET_ACCESS_KEY=...")
        print("     S3_READY=True")
        print("\n  5. Restart backend: docker restart aiflow_backend_local")
        sys.exit(1)
    else:
        print_success(message)
    
    # Step 4: Test actual S3 connection
    print_info("\nStep 4: Testing AWS S3 connection...")
    
    if config_data['S3_READY'].lower() == 'false':
        print_warning("S3_READY=False - Skipping connection test")
        print_info("Set S3_READY=True in .env.local to enable S3 storage")
        print_success("\n✓ Configuration validated (S3 disabled, using local storage)")
        return
    
    success = test_s3_connection(
        config_data['AWS_ACCESS_KEY_ID'],
        config_data['AWS_SECRET_ACCESS_KEY'],
        config_data['AWS_STORAGE_BUCKET_NAME'],
        config_data['AWS_S3_REGION_NAME']
    )
    
    if success:
        print_success("\n✓ All tests passed! S3 is properly configured and working.")
        print_info("\nTo activate S3 storage:")
        print("  1. Ensure S3_READY=True in .env.local")
        print("  2. Restart backend: docker restart aiflow_backend_local")
        print("  3. Check logs: docker logs aiflow_backend_local | grep S3")
    else:
        print_error("\n✗ S3 connection test failed")
        print_warning("Using local storage as fallback (safe mode)")
        print_info("\nTo fix and retry:")
        print("  1. Verify credentials in AWS IAM Console")
        print("  2. Update .env.local with correct credentials")
        print("  3. Run this script again: python configure_s3.py")
        sys.exit(1)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user{RESET}")
        sys.exit(0)
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")
        sys.exit(1)
