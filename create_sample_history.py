"""Create sample files for shareeq user in S3"""
import os
import django
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.crs_documents.helpers.user_storage import get_user_storage
from io import BytesIO

User = get_user_model()

print("=" * 60)
print("CREATING SAMPLE S3 FILES FOR SHAREEQ")
print("=" * 60)

try:
    user = User.objects.get(email='shareeq@rejlers.ae')
    print(f"\n✅ User found: {user.username} (ID: {user.id})")
    
    storage = get_user_storage(user)
    
    # Create sample uploads
    print("\n1. Creating sample uploads...")
    sample_uploads = [
        ("sample_crs_document_1.pdf", b"Sample CRS document content 1 - This is a test PDF file"),
        ("project_specifications.docx", b"Sample Word document content - Project Specifications"),
        ("data_analysis.xlsx", b"Sample Excel content - Data Analysis Report"),
    ]
    
    for filename, content in sample_uploads:
        result = storage.save_upload(
            file_content=content,
            original_filename=filename,
            metadata={'source': 'test_script', 'type': 'sample'}
        )
        if result.get('success'):
            print(f"   ✅ Uploaded: {filename} -> {result.get('s3_key')}")
        else:
            print(f"   ❌ Failed: {filename} - {result.get('error')}")
    
    # Create sample exports
    print("\n2. Creating sample exports...")
    sample_exports = [
        ("crs_report_summary", "xlsx", b"Sample Excel export - CRS Report Summary"),
        ("document_list", "csv", b"Sample CSV export - Document List\nID,Name,Status\n1,Doc1,Active"),
        ("analysis_report", "pdf", b"Sample PDF export - Analysis Report"),
    ]
    
    for base_filename, format_type, content in sample_exports:
        result = storage.save_export(
            file_content=content,
            export_format=format_type,
            base_filename=base_filename,
            metadata={'source': 'test_script', 'type': 'sample_export'}
        )
        if result.get('success'):
            print(f"   ✅ Exported: {base_filename}.{format_type} -> {result.get('s3_key')}")
        else:
            print(f"   ❌ Failed: {base_filename}.{format_type} - {result.get('error')}")
    
    # Get summary
    print("\n3. Verifying created files...")
    uploads = storage.get_user_uploads(limit=10)
    exports = storage.get_user_exports(limit=10)
    activities = storage.get_activity_history(days=1)
    
    print(f"   Total uploads: {len(uploads)}")
    print(f"   Total exports: {len(exports)}")
    print(f"   Total activities: {len(activities)}")
    
    print("\n✅ Sample files created successfully!")
    print("\nYou can now view them at:")
    print("   http://localhost:3000/crs/documents/history")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
