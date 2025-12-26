"""
Test PFD to P&ID Workflow
Comprehensive testing script for the PFD conversion system
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd.services.s3_pfd_manager import get_s3_pfd_manager
from apps.pfd_converter.models import PFDDocument, PIDConversion
from django.contrib.auth import get_user_model
import json

User = get_user_model()

def test_s3_connectivity():
    """Test S3 bucket connectivity and list files"""
    print("\n" + "="*60)
    print("TEST 1: S3 Bucket Connectivity")
    print("="*60)
    
    try:
        manager = get_s3_pfd_manager()
        info = manager.get_bucket_structure_info()
        
        print(f"✅ S3 Connection Successful!")
        print(f"   Bucket: {info['bucket_name']}")
        print(f"   Region: {info['region']}")
        print(f"   PFD Folder: {info['pfd_folder']}")
        print(f"   PID Folder: {info['pid_folder']}")
        print(f"   PFD Count: {info['pfd_count']}")
        print(f"   PID Count: {info['pid_count']}")
        
        # List PFD files
        if info['pfd_count'] > 0:
            print("\n📄 PFD Files:")
            pfd_files = manager.list_pfd_files(limit=5)
            for i, file in enumerate(pfd_files, 1):
                size_mb = file['size'] / (1024 * 1024)
                print(f"   {i}. {file['filename']} ({size_mb:.2f} MB)")
                print(f"      Has P&ID: {'✅' if file['has_pid_conversion'] else '❌'}")
        
        # List P&ID files
        if info['pid_count'] > 0:
            print("\n📊 P&ID Files:")
            pid_files = manager.list_pid_files(limit=5)
            for i, file in enumerate(pid_files, 1):
                size_mb = file['size'] / (1024 * 1024)
                print(f"   {i}. {file['filename']} ({size_mb:.2f} MB)")
        
        return True
    except Exception as e:
        print(f"❌ S3 Connection Failed: {e}")
        return False


def test_database_models():
    """Test database models and existing data"""
    print("\n" + "="*60)
    print("TEST 2: Database Models & Existing Data")
    print("="*60)
    
    try:
        # Check PFD Documents
        pfd_count = PFDDocument.objects.count()
        print(f"\n📋 PFD Documents in Database: {pfd_count}")
        
        if pfd_count > 0:
            recent_pfds = PFDDocument.objects.all().order_by('-created_at')[:5]
            for i, pfd in enumerate(recent_pfds, 1):
                print(f"\n   {i}. {pfd.document_number or 'N/A'} - {pfd.file_name}")
                print(f"      Status: {pfd.status}")
                print(f"      Uploaded by: {pfd.uploaded_by.username}")
                print(f"      Created: {pfd.created_at.strftime('%Y-%m-%d %H:%M')}")
                if pfd.extracted_data:
                    equipment_count = len(pfd.extracted_data.get('equipment', []))
                    print(f"      Equipment extracted: {equipment_count}")
        
        # Check P&ID Conversions
        pid_count = PIDConversion.objects.count()
        print(f"\n🔄 P&ID Conversions in Database: {pid_count}")
        
        if pid_count > 0:
            recent_pids = PIDConversion.objects.all().order_by('-created_at')[:5]
            for i, pid in enumerate(recent_pids, 1):
                print(f"\n   {i}. {pid.pid_drawing_number}")
                print(f"      Status: {pid.status}")
                print(f"      Source PFD: {pid.pfd_document.document_number}")
                print(f"      Created: {pid.created_at.strftime('%Y-%m-%d %H:%M')}")
        
        # Check Users
        user_count = User.objects.count()
        print(f"\n👤 Total Users: {user_count}")
        
        return True
    except Exception as e:
        print(f"❌ Database Test Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_availability():
    """Test if API endpoints are configured"""
    print("\n" + "="*60)
    print("TEST 3: API Endpoint Configuration")
    print("="*60)
    
    try:
        from django.urls import get_resolver
        from django.urls.resolvers import URLPattern, URLResolver
        
        def get_urls(urlpatterns, prefix=''):
            urls = []
            for pattern in urlpatterns:
                if isinstance(pattern, URLPattern):
                    urls.append(prefix + str(pattern.pattern))
                elif isinstance(pattern, URLResolver):
                    urls.extend(get_urls(pattern.url_patterns, prefix + str(pattern.pattern)))
            return urls
        
        resolver = get_resolver()
        all_urls = get_urls(resolver.url_patterns)
        
        # Filter PFD related URLs
        pfd_urls = [url for url in all_urls if 'pfd' in url.lower()]
        
        print(f"\n📡 PFD-Related API Endpoints ({len(pfd_urls)} found):")
        print("\nDatabase-Based APIs:")
        db_apis = [url for url in pfd_urls if '/api/v1/pfd/' in url and '/s3/' not in url]
        for url in sorted(db_apis)[:10]:
            print(f"   • {url}")
        
        print("\nS3-Based APIs:")
        s3_apis = [url for url in pfd_urls if '/api/v1/pfd/s3/' in url]
        for url in sorted(s3_apis):
            print(f"   • {url}")
        
        return True
    except Exception as e:
        print(f"❌ API Test Failed: {e}")
        return False


def test_openai_config():
    """Test OpenAI configuration"""
    print("\n" + "="*60)
    print("TEST 4: OpenAI Configuration")
    print("="*60)
    
    try:
        from decouple import config
        
        api_key = config('OPENAI_API_KEY', default='')
        model = config('OPENAI_MODEL', default='gpt-4o')
        
        if api_key:
            masked_key = api_key[:10] + "..." + api_key[-4:]
            print(f"✅ OpenAI API Key: {masked_key}")
        else:
            print(f"⚠️  OpenAI API Key: Not configured")
        
        print(f"📝 Model: {model}")
        
        # Try to initialize the converter
        from apps.pfd_converter.services import PFDToPIDConverter
        converter = PFDToPIDConverter()
        print(f"✅ PFDToPIDConverter initialized successfully")
        
        return bool(api_key)
    except Exception as e:
        print(f"❌ OpenAI Config Test Failed: {e}")
        return False


def test_file_storage():
    """Test file storage configuration"""
    print("\n" + "="*60)
    print("TEST 5: File Storage Configuration")
    print("="*60)
    
    try:
        from django.conf import settings
        
        print(f"📁 Media Root: {settings.MEDIA_ROOT}")
        print(f"🔗 Media URL: {settings.MEDIA_URL}")
        
        # Check if media directories exist
        pfd_dir = os.path.join(settings.MEDIA_ROOT, 'pfd_documents')
        pid_dir = os.path.join(settings.MEDIA_ROOT, 'pid_generated')
        
        if os.path.exists(pfd_dir):
            pfd_files = os.listdir(pfd_dir) if os.path.isdir(pfd_dir) else []
            print(f"✅ PFD Directory exists: {len(pfd_files)} files")
        else:
            print(f"⚠️  PFD Directory doesn't exist (will be created on upload)")
        
        if os.path.exists(pid_dir):
            pid_files = os.listdir(pid_dir) if os.path.isdir(pid_dir) else []
            print(f"✅ P&ID Directory exists: {len(pid_files)} files")
        else:
            print(f"⚠️  P&ID Directory doesn't exist (will be created on conversion)")
        
        return True
    except Exception as e:
        print(f"❌ File Storage Test Failed: {e}")
        return False


def print_summary(results):
    """Print test summary"""
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    passed = sum(results.values())
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All systems operational! Ready to process PFD files.")
        print("\n📝 Next Steps:")
        print("   1. Navigate to: http://localhost:3000/pfd/upload")
        print("   2. Upload a PFD file (PDF, JPG, or PNG)")
        print("   3. Monitor processing in backend logs")
        print("   4. View results in conversion page")
    else:
        print("\n⚠️  Some tests failed. Please check configuration.")


def main():
    """Run all tests"""
    print("\n" + "🔬" + " "*58 + "🔬")
    print("  PFD TO P&ID WORKFLOW - COMPREHENSIVE TEST SUITE")
    print("🔬" + " "*58 + "🔬")
    
    results = {
        "S3 Connectivity": test_s3_connectivity(),
        "Database Models": test_database_models(),
        "API Endpoints": test_api_availability(),
        "OpenAI Config": test_openai_config(),
        "File Storage": test_file_storage(),
    }
    
    print_summary(results)


if __name__ == "__main__":
    main()
