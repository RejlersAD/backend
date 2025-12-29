"""List all S3 files by user"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.core.s3_service import S3Service

s3 = S3Service()

# List all users
response = s3.s3_client.list_objects_v2(
    Bucket='user-management-rejlers',
    Prefix='users/',
    Delimiter='/'
)

print("=" * 60)
print("S3 BUCKET STRUCTURE - Users")
print("=" * 60)

# Get user folders
user_folders = []
for prefix in response.get('CommonPrefixes', []):
    folder = prefix['Prefix']
    if folder.startswith('users/') and folder.count('/') == 2:
        user_id = folder.split('/')[1]
        if user_id.isdigit():
            user_folders.append(int(user_id))

print(f"\nFound {len(user_folders)} users with data:")
for user_id in sorted(user_folders):
    print(f"\n--- USER {user_id} ---")
    
    # List all files for this user
    user_response = s3.s3_client.list_objects_v2(
        Bucket='user-management-rejlers',
        Prefix=f'users/{user_id}/',
        MaxKeys=100
    )
    
    files = user_response.get('Contents', [])
    print(f"Total objects: {len(files)}")
    
    # Categorize files
    uploads = [f for f in files if '/uploads/' in f['Key'] and not f['Key'].endswith('/')]
    exports = [f for f in files if '/exports/' in f['Key'] and not f['Key'].endswith('/')]
    history = [f for f in files if '/history/' in f['Key'] and not f['Key'].endswith('/')]
    
    print(f"  Uploads: {len(uploads)}")
    for f in uploads[:5]:
        print(f"    - {f['Key'].split('/')[-1]} ({f['Size']} bytes)")
    
    print(f"  Exports: {len(exports)}")
    for f in exports[:5]:
        print(f"    - {f['Key'].split('/')[-1]} ({f['Size']} bytes)")
    
    print(f"  History: {len(history)}")
    for f in history[:5]:
        print(f"    - {f['Key'].split('/')[-1]} ({f['Size']} bytes)")

print("\n" + "=" * 60)
