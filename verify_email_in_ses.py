"""
Script to verify an email address in AWS SES for testing
Run this script to add xerxez.in@gmail.com as a verified email recipient
"""
import boto3
import os

def verify_email_address(email):
    """Verify an email address in AWS SES"""
    try:
        # Initialize SES client
        ses_client = boto3.client(
            'ses',
            region_name='ap-south-1',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        
        # Request verification
        response = ses_client.verify_email_identity(
            EmailAddress=email
        )
        
        print(f"✅ Verification email sent to {email}")
        print(f"📧 Check the inbox and click the verification link")
        print(f"📋 Response: {response}")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to verify email: {e}")
        return False

def check_verification_status(email):
    """Check if an email address is verified"""
    try:
        ses_client = boto3.client(
            'ses',
            region_name='ap-south-1',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        
        response = ses_client.get_identity_verification_attributes(
            Identities=[email]
        )
        
        status = response['VerificationAttributes'].get(email, {}).get('VerificationStatus', 'NotStarted')
        print(f"📊 Verification status for {email}: {status}")
        
        return status == 'Success'
        
    except Exception as e:
        print(f"❌ Failed to check status: {e}")
        return False

if __name__ == "__main__":
    email = "xerxez.in@gmail.com"
    
    print("=" * 60)
    print("AWS SES Email Verification")
    print("=" * 60)
    
    # Check current status
    print("\n1. Checking current verification status...")
    is_verified = check_verification_status(email)
    
    if is_verified:
        print(f"✅ {email} is already verified!")
    else:
        print(f"\n2. Sending verification request...")
        verify_email_address(email)
        print("\n" + "=" * 60)
        print("NEXT STEPS:")
        print("=" * 60)
        print(f"1. Check the inbox for {email}")
        print("2. Click the verification link in the email from AWS")
        print("3. After verification, try creating the user again")
        print("=" * 60)
