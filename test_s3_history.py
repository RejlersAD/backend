"""Test S3 history functionality"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.core.s3_service import S3Service
from apps.crs_documents.helpers.user_storage import get_user_storage, UserStorageManager
from django.contrib.auth import get_user_model

User = get_user_model()

print("=" * 60)
print("TESTING S3 HISTORY FUNCTIONALITY")
print("=" * 60)

# Test 1: S3 Connection
print("\n1. Testing S3 Connection...")
try:
    s3 = S3Service()
    response = s3.s3_client.list_objects_v2(
        Bucket='user-management-rejlers',
        Prefix='users/',
        MaxKeys=10
    )
    print(f"✅ S3 Connection: OK")
    print(f"   Files found in 'users/' prefix: {len(response.get('Contents', []))}")
    for obj in response.get('Contents', [])[:5]:
        print(f"   - {obj['Key']}")
except Exception as e:
    print(f"❌ S3 Connection failed: {e}")

# Test 2: Get user
print("\n2. Testing User Storage Manager...")
try:
    user = User.objects.get(email='shareeq@rejlers.ae')
    print(f"✅ User found: {user.username} (ID: {user.id})")
    
    storage = get_user_storage(user)
    print(f"✅ Storage manager created")
    print(f"   User base path: {storage.user_base_path}")
    print(f"   Uploads path: {storage.uploads_path}")
    print(f"   Exports path: {storage.exports_path}")
    print(f"   History path: {storage.history_path}")
    
except Exception as e:
    print(f"❌ User storage manager failed: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Ensure user folders
print("\n3. Testing Folder Creation...")
try:
    result = storage.ensure_user_folders()
    print(f"✅ Folders ensured: {result}")
except Exception as e:
    print(f"❌ Folder creation failed: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Get user profile
print("\n4. Testing User Profile...")
try:
    profile = storage.get_user_profile()
    print(f"✅ User profile retrieved:")
    if profile:
        for key, value in profile.items():
            print(f"   {key}: {value}")
    else:
        print("   Profile is empty or None")
except Exception as e:
    print(f"❌ Profile retrieval failed: {e}")
    import traceback
    traceback.print_exc()

# Test 5: List uploads
print("\n5. Testing Uploads Listing...")
try:
    uploads = storage.get_user_uploads(limit=10)
    print(f"✅ Uploads retrieved: {len(uploads)} files")
    for upload in uploads[:3]:
        print(f"   - {upload.get('filename')} ({upload.get('size')} bytes)")
except Exception as e:
    print(f"❌ Uploads listing failed: {e}")
    import traceback
    traceback.print_exc()

# Test 6: List exports
print("\n6. Testing Exports Listing...")
try:
    exports = storage.get_user_exports(limit=10)
    print(f"✅ Exports retrieved: {len(exports)} files")
    for export in exports[:3]:
        print(f"   - {export.get('filename')} ({export.get('format')})")
except Exception as e:
    print(f"❌ Exports listing failed: {e}")
    import traceback
    traceback.print_exc()

# Test 7: Get activity history
print("\n7. Testing Activity History...")
try:
    activities = storage.get_activity_history(days=30)
    print(f"✅ Activities retrieved: {len(activities)} activities")
    for activity in activities[:3]:
        print(f"   - {activity.get('action')} at {activity.get('timestamp')}")
except Exception as e:
    print(f"❌ Activity history failed: {e}")
    import traceback
    traceback.print_exc()

# Test 8: List all files for user in S3
print("\n8. Testing Direct S3 Listing for User...")
try:
    s3 = S3Service()
    response = s3.s3_client.list_objects_v2(
        Bucket='user-management-rejlers',
        Prefix=f'users/{user.id}/',
        MaxKeys=50
    )
    print(f"✅ Direct S3 listing:")
    print(f"   Total files for user {user.id}: {len(response.get('Contents', []))}")
    for obj in response.get('Contents', [])[:10]:
        print(f"   - {obj['Key']} ({obj['Size']} bytes)")
except Exception as e:
    print(f"❌ Direct S3 listing failed: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
