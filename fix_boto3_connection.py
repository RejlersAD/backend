"""
Boto3 Connection Troubleshooting & Fixes
Diagnoses and fixes common boto3 connectivity issues
"""
import os
import sys


def check_and_fix_boto3():
    """
    Diagnose and fix boto3 connection issues
    """
    print("\n" + "="*60)
    print("BOTO3 CONNECTION TROUBLESHOOTING")
    print("="*60)
    
    issues_found = []
    fixes_applied = []
    
    # Check 1: boto3 installation
    print("\n[Check 1] Verifying boto3 installation...")
    try:
        import boto3
        print(f"✓ boto3 {boto3.__version__} is installed")
    except ImportError:
        issues_found.append("boto3 not installed")
        print("✗ boto3 is NOT installed")
        print("\nFix: Install boto3")
        print("   pip install boto3")
        return
    
    # Check 2: botocore installation
    print("\n[Check 2] Verifying botocore...")
    try:
        import botocore
        print(f"✓ botocore {botocore.__version__} is installed")
    except ImportError:
        issues_found.append("botocore not installed")
        print("✗ botocore is NOT installed")
        print("\nFix: Install botocore")
        print("   pip install botocore")
        return
    
    # Check 3: AWS credentials
    print("\n[Check 3] Checking AWS credentials...")
    aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    if not aws_access_key:
        issues_found.append("AWS_ACCESS_KEY_ID not set")
        print("✗ AWS_ACCESS_KEY_ID is NOT set")
    else:
        print(f"✓ AWS_ACCESS_KEY_ID: {aws_access_key[:8]}...{aws_access_key[-4:]}")
    
    if not aws_secret_key:
        issues_found.append("AWS_SECRET_ACCESS_KEY not set")
        print("✗ AWS_SECRET_ACCESS_KEY is NOT set")
    else:
        print(f"✓ AWS_SECRET_ACCESS_KEY: {'*' * 20}")
    
    # Check 4: Bucket configuration
    print("\n[Check 4] Checking bucket configuration...")
    main_bucket = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    main_region = os.environ.get('AWS_S3_REGION_NAME')
    pfd_bucket = os.environ.get('PFD_S3_BUCKET')
    pfd_region = os.environ.get('PFD_S3_REGION')
    
    if not main_bucket:
        issues_found.append("AWS_STORAGE_BUCKET_NAME not set")
        print("✗ AWS_STORAGE_BUCKET_NAME is NOT set")
    else:
        print(f"✓ Main bucket: {main_bucket}")
    
    if not main_region:
        print("⚠ AWS_S3_REGION_NAME not set (will use default: us-east-1)")
    else:
        print(f"✓ Main region: {main_region}")
    
    if not pfd_bucket:
        print("⚠ PFD_S3_BUCKET not set (will use default: rejlers-edrs-project)")
    else:
        print(f"✓ PFD bucket: {pfd_bucket}")
    
    if not pfd_region:
        print("⚠ PFD_S3_REGION not set (will use default: ap-south-1)")
    else:
        print(f"✓ PFD region: {pfd_region}")
    
    # Check 5: Test connection
    print("\n[Check 5] Testing S3 connection...")
    if aws_access_key and aws_secret_key and main_bucket:
        try:
            from botocore.exceptions import ClientError, NoCredentialsError
            
            s3_client = boto3.client(
                's3',
                region_name=main_region or 'us-east-1',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
            
            # Try to access bucket
            s3_client.head_bucket(Bucket=main_bucket)
            print(f"✓ Successfully connected to {main_bucket}")
            
            # Try to list objects
            response = s3_client.list_objects_v2(Bucket=main_bucket, MaxKeys=1)
            print(f"✓ Can list objects in bucket")
            
        except NoCredentialsError:
            issues_found.append("Invalid AWS credentials")
            print("✗ AWS credentials are invalid")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '403':
                issues_found.append("Access denied to bucket")
                print(f"✗ Access denied to bucket: {main_bucket}")
                print("   Check IAM permissions for the credentials")
            elif error_code == '404':
                issues_found.append("Bucket not found")
                print(f"✗ Bucket not found: {main_bucket}")
            else:
                issues_found.append(f"S3 error: {error_code}")
                print(f"✗ S3 Error: {e}")
        except Exception as e:
            issues_found.append(f"Connection error: {str(e)}")
            print(f"✗ Connection error: {e}")
    else:
        print("⚠ Skipping connection test (missing configuration)")
    
    # Check 6: Network connectivity
    print("\n[Check 6] Checking network connectivity to AWS...")
    try:
        import socket
        socket.create_connection(("s3.amazonaws.com", 443), timeout=5)
        print("✓ Can reach AWS S3 endpoints")
    except Exception as e:
        issues_found.append("Network connectivity issue")
        print(f"✗ Cannot reach AWS S3 endpoints: {e}")
        print("   Check firewall/proxy settings")
    
    # Summary
    print("\n" + "="*60)
    print("DIAGNOSIS SUMMARY")
    print("="*60)
    
    if not issues_found:
        print("\n✓ No issues found! Boto3 connectivity is working correctly.")
    else:
        print(f"\n✗ Found {len(issues_found)} issue(s):")
        for i, issue in enumerate(issues_found, 1):
            print(f"   {i}. {issue}")
        
        print("\n" + "="*60)
        print("RECOMMENDED FIXES")
        print("="*60)
        
        if "boto3 not installed" in issues_found:
            print("\n1. Install boto3:")
            print("   pip install boto3")
        
        if "AWS_ACCESS_KEY_ID not set" in issues_found or "AWS_SECRET_ACCESS_KEY not set" in issues_found:
            print("\n2. Set AWS credentials in docker-compose.yml:")
            print("   environment:")
            print("     - AWS_ACCESS_KEY_ID=<your-access-key>")
            print("     - AWS_SECRET_ACCESS_KEY=<your-secret-key>")
        
        if "AWS_STORAGE_BUCKET_NAME not set" in issues_found:
            print("\n3. Set bucket name:")
            print("   environment:")
            print("     - AWS_STORAGE_BUCKET_NAME=user-management-rejlers")
            print("     - AWS_S3_REGION_NAME=us-east-1")
        
        if "Access denied to bucket" in str(issues_found):
            print("\n4. Check IAM permissions. Required permissions:")
            print("   - s3:ListBucket")
            print("   - s3:GetObject")
            print("   - s3:PutObject")
            print("   - s3:DeleteObject")
            print("   - s3:GetBucketLocation")
        
        if "Network connectivity issue" in str(issues_found):
            print("\n5. Check network settings:")
            print("   - Ensure port 443 is open")
            print("   - Check proxy settings")
            print("   - Verify DNS resolution")
    
    print("\n")


if __name__ == "__main__":
    check_and_fix_boto3()
