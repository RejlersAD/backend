"""
S3 Configuration Diagnostic Script
Checks if backend is properly configured to use S3 for history storage
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from apps.crs_documents.helpers.user_storage import S3_AVAILABLE, S3Service

print("=" * 70)
print("S3 CONFIGURATION DIAGNOSTIC")
print("=" * 70)

# Check environment variables
print("\n1. Environment Variables:")
print(f"   USE_S3: {os.getenv('USE_S3', 'Not set')}")
print(f"   AWS_ACCESS_KEY_ID: {'Set' if os.getenv('AWS_ACCESS_KEY_ID') else 'NOT SET'}")
print(f"   AWS_SECRET_ACCESS_KEY: {'Set' if os.getenv('AWS_SECRET_ACCESS_KEY') else 'NOT SET'}")
print(f"   AWS_STORAGE_BUCKET_NAME: {os.getenv('AWS_STORAGE_BUCKET_NAME', 'Not set')}")
print(f"   AWS_S3_REGION_NAME: {os.getenv('AWS_S3_REGION_NAME', 'Not set')}")

# Check S3Service availability
print(f"\n2. S3Service Status:")
print(f"   S3_AVAILABLE: {S3_AVAILABLE}")
if S3_AVAILABLE:
    print("   ✓ S3Service imported successfully")
    try:
        s3_service = S3Service()
        print(f"   ✓ S3Service initialized")
        print(f"   ✓ Bucket: {s3_service.bucket_name}")
        print(f"   ✓ Region: {s3_service.region}")
        
        # Test bucket access
        try:
            s3_service.s3_client.head_bucket(Bucket=s3_service.bucket_name)
            print(f"   ✓ Bucket '{s3_service.bucket_name}' is accessible")
        except Exception as e:
            print(f"   ✗ Cannot access bucket: {e}")
    except Exception as e:
        print(f"   ✗ Failed to initialize S3Service: {e}")
else:
    print("   ✗ S3Service NOT available")

# Check user storage manager
print(f"\n3. User Storage Manager:")
try:
    from apps.crs_documents.helpers.user_storage import UserStorageManager
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    test_user = User.objects.first()
    
    if test_user:
        print(f"   Testing with user: {test_user.username} (ID: {test_user.id})")
        storage = UserStorageManager(user=test_user)
        print(f"   ✓ UserStorageManager initialized")
        print(f"   User base path: {storage.user_base_path}")
        print(f"   Uploads path: {storage.uploads_path}")
        print(f"   Exports path: {storage.exports_path}")
        print(f"   S3 service: {'Available' if storage.s3_service else 'NOT available'}")
        
        if storage.s3_service:
            # Try to ensure folders
            result = storage.ensure_user_folders()
            if result.get('success'):
                print("   ✓ User folders ensured successfully")
            else:
                print(f"   ✗ Failed to ensure folders: {result.get('reason', 'Unknown')}")
            
            # Try to get uploads
            uploads = storage.get_user_uploads(limit=5)
            print(f"   ✓ Can list uploads: {len(uploads)} file(s) found")
            
            # Try to get exports  
            exports = storage.get_user_exports(limit=5)
            print(f"   ✓ Can list exports: {len(exports)} file(s) found")
    else:
        print("   ⚠️  No users in database to test with")
        
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Check history API views
print(f"\n4. History API Views:")
try:
    from apps.crs_documents.history_api.history_views import (
        user_history_overview,
        user_uploads,
        user_exports,
    )
    print("   ✓ History API views imported successfully")
    print("   ✓ Available endpoints:")
    print("      - /api/v1/crs/documents/history/")
    print("      - /api/v1/crs/documents/history/uploads/")
    print("      - /api/v1/crs/documents/history/exports/")
    print("      - /api/v1/crs/documents/history/activity/")
    print("      - /api/v1/crs/documents/history/download/")
except Exception as e:
    print(f"   ✗ History API views NOT available: {e}")

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)

# Print recommendations
print("\n📋 Recommendations:")
if not S3_AVAILABLE:
    print("   ⚠️  Enable S3 by setting USE_S3=True in .env")
    print("   ⚠️  Restart backend after changing .env")
elif S3_AVAILABLE:
    print("   ✓ S3 is properly configured!")
    print("   ✓ History storage should work correctly")
    print("\n   Next steps:")
    print("   1. Upload a PDF file through CRS")
    print("   2. Check history page at http://localhost:3000/crs/documents/history")
    print("   3. Verify files appear in the uploads list")
