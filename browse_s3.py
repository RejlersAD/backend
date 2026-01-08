"""
Browse AWS S3 bucket - simplified version
"""
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def list_s3_bucket():
    """List files in S3 bucket using environment variables"""
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    
    try:
        # Get credentials from environment
        bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'aiflow')
        region = os.environ.get('AWS_S3_REGION_NAME', 'me-central-1')
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        # Check if credentials exist
        if not access_key or not secret_key:
            print("❌ AWS credentials not found in environment variables")
            print("\n💡 Please check your .env file or set environment variables:")
            print("   - AWS_ACCESS_KEY_ID")
            print("   - AWS_SECRET_ACCESS_KEY")
            print("   - AWS_STORAGE_BUCKET_NAME")
            print("   - AWS_S3_REGION_NAME")
            return
        
        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        print("="*80)
        print(f"AWS S3 BUCKET BROWSER")
        print("="*80)
        print(f"Bucket: {bucket_name}")
        print(f"Region: {region}")
        print("="*80)
        print()
        
        # Get search prefix from command line
        prefix = sys.argv[1] if len(sys.argv) > 1 else ''
        
        if prefix:
            print(f"🔍 Searching for: {prefix}")
        else:
            print(f"📂 Listing all files...")
        print()
        
        # List objects
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        
        files = []
        folders = set()
        
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    size = obj['Size']
                    modified = obj['LastModified']
                    
                    # Extract folder structure
                    if '/' in key:
                        folder_parts = key.split('/')[:-1]
                        folder_path = '/'.join(folder_parts)
                        folders.add(folder_path)
                    
                    files.append({
                        'key': key,
                        'size': size,
                        'modified': modified
                    })
        
        # Display folders
        if folders:
            print("📁 FOLDERS:")
            print("-" * 80)
            for folder in sorted(folders):
                # Count files in this folder
                folder_files = [f for f in files if f['key'].startswith(folder + '/')]
                print(f"   {folder}/ ({len(folder_files)} files)")
            print()
        
        # Display files
        if files:
            print(f"📄 FILES: {len(files)} total")
            print("-" * 80)
            
            # Group files by folder
            file_by_folder = {}
            for f in files:
                key = f['key']
                if '/' in key:
                    folder = '/'.join(key.split('/')[:-1])
                else:
                    folder = '(root)'
                
                if folder not in file_by_folder:
                    file_by_folder[folder] = []
                file_by_folder[folder].append(f)
            
            # Display grouped by folder
            for folder in sorted(file_by_folder.keys()):
                print(f"\n📂 {folder}/")
                for f in file_by_folder[folder]:
                    size_mb = f['size'] / (1024 * 1024)
                    filename = f['key'].split('/')[-1]
                    if size_mb > 1:
                        print(f"   ├─ {filename} ({size_mb:.2f} MB)")
                    else:
                        size_kb = f['size'] / 1024
                        print(f"   ├─ {filename} ({size_kb:.1f} KB)")
        else:
            print(f"⚠️  No files found")
            if prefix:
                print(f"   Try searching with different prefix or without prefix")
        
        print()
        print("="*80)
        
        # Suggest LEGEND_SHEET search
        if 'LEGEND_SHEET' not in prefix.upper():
            legend_files = [f for f in files if 'LEGEND' in f['key'].upper()]
            if legend_files:
                print(f"\n💡 Found {len(legend_files)} files with 'LEGEND' in name:")
                for f in legend_files[:5]:
                    print(f"   - {f['key']}")
                if len(legend_files) > 5:
                    print(f"   ... and {len(legend_files) - 5} more")
        
    except NoCredentialsError:
        print("❌ AWS credentials not configured")
    except ClientError as e:
        print(f"❌ AWS Error: {e}")
        print(f"   Error Code: {e.response['Error']['Code']}")
        print(f"   Message: {e.response['Error']['Message']}")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")

if __name__ == '__main__':
    list_s3_bucket()
