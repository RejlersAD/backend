"""
Quick Diagnostic: Check Spec Customization Async Setup
Run this in Django shell or as a management command to verify configuration
"""
import os
import sys

# Set Django settings before importing Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings
from django.core.cache import cache

def check_spec_async_setup():
    """Diagnose spec-customization async configuration"""
    
    print("=" * 70)
    print("SPEC CUSTOMIZATION ASYNC SETUP DIAGNOSTIC")
    print("=" * 70)
    print()
    
    issues = []
    warnings = []
    
    # 1. Check Redis
    print("1. REDIS CONNECTION")
    print("-" * 70)
    redis_url = getattr(settings, 'REDIS_URL', None) or os.getenv('REDIS_URL')
    if redis_url:
        print(f"✅ REDIS_URL configured: {redis_url[:50]}...")
        
        # Test Redis connection
        try:
            cache.set('__spec_test__', 'ok', 10)
            result = cache.get('__spec_test__')
            if result == 'ok':
                print("✅ Redis connection working")
                cache.delete('__spec_test__')
            else:
                print("❌ Redis test failed (write/read mismatch)")
                issues.append("Redis connection issue - cache not working")
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
            issues.append(f"Cannot connect to Redis: {e}")
    else:
        print("❌ REDIS_URL not configured")
        issues.append("REDIS_URL not set - Celery will run in EAGER (synchronous) mode")
    print()
    
    # 2. Check Celery Configuration
    print("2. CELERY CONFIGURATION")
    print("-" * 70)
    eager_mode = getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', True)
    broker_url = getattr(settings, 'CELERY_BROKER_URL', None)
    
    if eager_mode:
        print("❌ Celery running in EAGER mode (synchronous)")
        print("   Tasks will block HTTP requests!")
        issues.append("CELERY_TASK_ALWAYS_EAGER=True - tasks run synchronously")
    else:
        print("✅ Celery running in ASYNC mode")
    
    if broker_url:
        print(f"✅ Broker configured: {broker_url[:50]}...")
    else:
        print("❌ No broker configured")
        issues.append("CELERY_BROKER_URL not set")
    
    print()
    
    # 3. Check AWS S3
    print("3. AWS S3 CONFIGURATION (for large files)")
    print("-" * 70)
    s3_bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
    aws_key = getattr(settings, 'AWS_ACCESS_KEY_ID', '')
    
    if s3_bucket:
        print(f"✅ S3 Bucket: {s3_bucket}")
    else:
        print("⚠️  AWS_STORAGE_BUCKET_NAME not set")
        warnings.append("S3 not configured - uploads limited to ~100MB")
    
    if aws_key:
        print(f"✅ AWS credentials configured")
    else:
        print("⚠️  AWS_ACCESS_KEY_ID not set")
        warnings.append("AWS credentials missing")
    
    # Check presigned upload availability
    try:
        from apps.spec_customization.services.presigned_upload import is_presigned_upload_available
        if is_presigned_upload_available():
            print("✅ S3 presigned upload available")
        else:
            print("⚠️  S3 presigned upload not available")
            warnings.append("Direct S3 upload disabled - large files may fail")
    except Exception as e:
        print(f"⚠️  Cannot check presigned upload: {e}")
    
    print()
    
    # 4. Check AI API Keys
    print("4. AI API CONFIGURATION")
    print("-" * 70)
    openai_key = os.getenv('OPENAI_API_KEY', '')
    gemini_key = os.getenv('GOOGLE_API_KEY', '') or os.getenv('GEMINI_API_KEY', '')
    
    if openai_key:
        print(f"✅ OpenAI API key configured ({openai_key[:8]}...)")
    else:
        print("⚠️  OPENAI_API_KEY not set")
        warnings.append("OpenAI fallback unavailable")
    
    if gemini_key:
        print(f"✅ Gemini API key configured ({gemini_key[:8]}...)")
    else:
        print("❌ GOOGLE_API_KEY not set")
        issues.append("Gemini API key missing - primary AI engine unavailable")
    
    print()
    
    # 5. Check Spec Extraction Config
    print("5. EXTRACTION CONFIGURATION")
    print("-" * 70)
    try:
        from apps.spec_customization.services.config import SPEC_EXTRACTION_CONFIG
        print(f"Chunk size: {SPEC_EXTRACTION_CONFIG['chunk_size_pages']} pages")
        print(f"Parallel chunks: {SPEC_EXTRACTION_CONFIG['max_chunks_parallel']}")
        print(f"AI engines: {', '.join(SPEC_EXTRACTION_CONFIG['ai_engines'])}")
        print(f"Dedupe enabled: {SPEC_EXTRACTION_CONFIG['dedupe_by_sha256']}")
        print(f"Max AI pages/job: {SPEC_EXTRACTION_CONFIG['max_ai_pages_per_job']}")
    except Exception as e:
        print(f"⚠️  Cannot load extraction config: {e}")
    
    print()
    
    # 6. Environment Info
    print("6. ENVIRONMENT")
    print("-" * 70)
    env = os.getenv('AIFLOW_ENVIRONMENT', os.getenv('ENVIRONMENT', 'unknown'))
    debug = getattr(settings, 'DEBUG', False)
    print(f"Environment: {env}")
    print(f"Debug mode: {debug}")
    print(f"Database: {settings.DATABASES['default']['ENGINE']}")
    
    print()
    
    # SUMMARY
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    if not issues and not warnings:
        print("✅ ALL CHECKS PASSED!")
        print()
        print("Your spec-customization is configured for ASYNC processing.")
        print("Tasks will run in background and won't block requests.")
        print()
        print("Expected behavior:")
        print("  1. Upload returns in < 30 seconds")
        print("  2. Task queued to Celery worker")
        print("  3. Frontend polls for progress")
        print("  4. Worker processes in background (5-60 minutes)")
        print()
    else:
        if issues:
            print(f"❌ CRITICAL ISSUES ({len(issues)}):")
            for i, issue in enumerate(issues, 1):
                print(f"   {i}. {issue}")
            print()
        
        if warnings:
            print(f"⚠️  WARNINGS ({len(warnings)}):")
            for i, warning in enumerate(warnings, 1):
                print(f"   {i}. {warning}")
            print()
        
        print("RECOMMENDATION:")
        print()
        
        if "REDIS_URL not set" in str(issues):
            print("🔴 URGENT: Add Redis to Railway")
            print("   1. Railway Dashboard → + New Service → Redis")
            print("   2. Copy REDIS_URL from Redis service")
            print("   3. Add to backend service environment variables")
            print()
        
        if "CELERY_TASK_ALWAYS_EAGER=True" in str(issues):
            print("🔴 URGENT: Disable EAGER mode")
            print("   Set: CELERY_TASK_ALWAYS_EAGER=false")
            print("   Or ensure REDIS_URL is configured")
            print()
        
        if "Gemini API key missing" in str(issues):
            print("🔴 URGENT: Add AI API keys")
            print("   Set: GOOGLE_API_KEY=<your Gemini key>")
            print("   Set: OPENAI_API_KEY=<your OpenAI key>")
            print()
        
        if not issues and warnings:
            print("✅ No critical issues - system will work but:")
            print("   - Large file uploads may be limited")
            print("   - Some AI engines may be unavailable")
    
    print()
    print("=" * 70)
    print("For full setup guide, see: SPEC_CUSTOMIZATION_TIMEOUT_SOLUTION.md")
    print("=" * 70)
    
    return len(issues) == 0

if __name__ == '__main__':
    # Allow running as standalone script
    import django
    django.setup()
    check_spec_async_setup()
