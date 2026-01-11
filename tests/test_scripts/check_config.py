"""
AIFlow Configuration Diagnostic Tool
Checks if all required configurations are properly set
"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_config():
    """Check all critical configurations"""
    print("="*70)
    print("AIFlow Configuration Diagnostic")
    print("="*70)
    
    issues = []
    warnings = []
    
    # 1. Check OpenAI API Key
    print("\n1. OpenAI API Key")
    openai_key = os.getenv('OPENAI_API_KEY', '')
    if not openai_key or openai_key == 'your-openai-api-key-here':
        print("   ❌ NOT CONFIGURED")
        issues.append("OpenAI API key is missing or using placeholder")
        print("   → Action: Get API key from https://platform.openai.com/api-keys")
        print("   → Add to .env: OPENAI_API_KEY=sk-proj-your-key-here")
    else:
        print(f"   ✅ CONFIGURED ({openai_key[:8]}...{openai_key[-4:]})")
        if not openai_key.startswith(('sk-', 'sk-proj-')):
            warnings.append("OpenAI API key format looks unusual")
    
    # 2. Check Database Configuration
    print("\n2. Database Configuration")
    db_url = os.getenv('DATABASE_URL', '')
    db_host = os.getenv('DB_HOST', '')
    if db_url or db_host:
        print(f"   ✅ CONFIGURED")
        if db_url:
            # Mask password in URL
            masked_url = db_url.split('@')[1] if '@' in db_url else 'configured'
            print(f"   → Using: Railway PostgreSQL ({masked_url})")
    else:
        print("   ⚠️  WARNING: Database not configured")
        warnings.append("Database configuration not found")
    
    # 3. Check Admin Credentials
    print("\n3. Admin Users Configuration")
    admin1_email = os.getenv('ADMIN_EMAIL', '')
    admin2_email = os.getenv('ADMIN2_EMAIL', '')
    
    if admin1_email:
        print(f"   ✅ Primary Admin: {admin1_email}")
    else:
        warnings.append("Primary admin not configured")
    
    if admin2_email:
        print(f"   ✅ Secondary Admin: {admin2_email}")
    else:
        print("   ℹ️  Secondary admin: Not configured (optional)")
    
    # 4. Check Django Settings
    print("\n4. Django Settings")
    secret_key = os.getenv('SECRET_KEY', '')
    debug = os.getenv('DEBUG', 'False')
    allowed_hosts = os.getenv('ALLOWED_HOSTS', '')
    
    if secret_key and secret_key != 'your-secret-key-change-this-in-production':
        print(f"   ✅ Secret Key: Configured")
    else:
        warnings.append("Django SECRET_KEY is using default value")
    
    print(f"   ℹ️  Debug Mode: {debug}")
    print(f"   ℹ️  Allowed Hosts: {allowed_hosts or 'Default'}")
    
    # 5. Check CORS Settings
    print("\n5. CORS & Security Settings")
    cors_origins = os.getenv('CORS_ALLOWED_ORIGINS', '')
    if cors_origins:
        print(f"   ✅ CORS Origins: {cors_origins}")
    else:
        warnings.append("CORS settings not configured")
    
    # 6. Check AWS S3 (Optional)
    print("\n6. AWS S3 Storage (Optional)")
    use_s3 = os.getenv('USE_S3', 'False')
    aws_key = os.getenv('AWS_ACCESS_KEY_ID', '')
    if use_s3 == 'True':
        if aws_key and aws_key != 'your-aws-key':
            print(f"   ✅ S3 Enabled and Configured")
        else:
            warnings.append("S3 enabled but AWS credentials not configured")
    else:
        print(f"   ℹ️  S3 Storage: Disabled (using local storage)")
    
    # Summary
    print("\n" + "="*70)
    print("DIAGNOSTIC SUMMARY")
    print("="*70)
    
    if not issues and not warnings:
        print("✅ All configurations look good!")
        print("\nNext steps:")
        print("  1. Test P&ID analysis with a sample drawing")
        print("  2. Verify multi-user access")
        print("  3. Check error handling with invalid inputs")
    else:
        if issues:
            print(f"\n❌ CRITICAL ISSUES ({len(issues)}):")
            for i, issue in enumerate(issues, 1):
                print(f"   {i}. {issue}")
        
        if warnings:
            print(f"\n⚠️  WARNINGS ({len(warnings)}):")
            for i, warning in enumerate(warnings, 1):
                print(f"   {i}. {warning}")
        
        print("\n📋 ACTION REQUIRED:")
        if issues:
            print("   → Fix critical issues before testing")
        if warnings:
            print("   → Review warnings (may affect functionality)")
    
    print("\n" + "="*70)
    
    # Test OpenAI Connection (if key is configured)
    if openai_key and openai_key != 'your-openai-api-key-here':
        print("\n🔍 Testing OpenAI Connection...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            # Simple test to verify key
            response = client.models.list()
            print("   ✅ OpenAI connection successful!")
            print("   → API key is valid and active")
        except Exception as e:
            print(f"   ❌ OpenAI connection failed: {str(e)}")
            if "invalid_api_key" in str(e).lower():
                print("   → API key is invalid or expired")
            elif "quota" in str(e).lower():
                print("   → Insufficient quota or credits")
            print("   → Fix this before running P&ID analysis")
    
    print("="*70 + "\n")
    
    return len(issues) == 0


if __name__ == '__main__':
    try:
        success = check_config()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Diagnostic failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
