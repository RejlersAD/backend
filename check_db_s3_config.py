"""
Smart Database and S3 Configuration Checker
Validates data persistence to Railway PostgreSQL and AWS S3
"""
import os
import django
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from apps.procurement.models import PurchaseRequisition, PurchaseOrder, Vendor
from django.core.files.storage import default_storage

def check_database_config():
    """Check current database configuration"""
    print("\n" + "="*70)
    print("📊 DATABASE CONFIGURATION CHECK")
    print("="*70)
    
    db_config = settings.DATABASES['default']
    print(f"\n✓ Database Engine: {db_config['ENGINE']}")
    print(f"✓ Database Name: {db_config['NAME']}")
    print(f"✓ Database Host: {db_config['HOST']}")
    print(f"✓ Database Port: {db_config['PORT']}")
    print(f"✓ Database User: {db_config['USER']}")
    
    # Determine if using Railway or Local
    if 'railway' in db_config['HOST'].lower() or 'rlwy' in db_config['HOST'].lower():
        print(f"\n🚂 CONNECTION TYPE: Railway PostgreSQL (Production)")
        print(f"   Host: {db_config['HOST']}")
    elif db_config['HOST'] == 'db' or db_config['HOST'] == 'localhost':
        print(f"\n🐳 CONNECTION TYPE: Local PostgreSQL (Docker/Development)")
        print(f"   Host: {db_config['HOST']}")
    else:
        print(f"\n🔧 CONNECTION TYPE: Custom PostgreSQL")
        print(f"   Host: {db_config['HOST']}")

def check_s3_config():
    """Check AWS S3 configuration"""
    print("\n" + "="*70)
    print("☁️  AWS S3 CONFIGURATION CHECK")
    print("="*70)
    
    use_s3 = getattr(settings, 'USE_S3', False)
    s3_ready = getattr(settings, 'S3_READY', False)
    
    print(f"\n✓ USE_S3: {use_s3}")
    print(f"✓ S3_READY: {s3_ready}")
    
    if use_s3 and s3_ready:
        print(f"✓ AWS Access Key: {'Configured' if settings.AWS_ACCESS_KEY_ID else 'Not set'}")
        print(f"✓ AWS Bucket: {getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'Not configured')}")
        print(f"✓ AWS Region: {getattr(settings, 'AWS_S3_REGION_NAME', 'Not configured')}")
        print(f"\n☁️  STORAGE MODE: AWS S3 (Production)")
        print(f"   Files will be uploaded to S3 bucket")
    elif use_s3 and not s3_ready:
        print(f"\n⚠️  STORAGE MODE: Local Storage (Safety Mode)")
        print(f"   USE_S3=True but S3_READY=False")
        print(f"   Files saved locally until S3_READY=True")
    else:
        print(f"\n💾 STORAGE MODE: Local Storage (Development)")
        print(f"   Files saved to local filesystem")
    
    # Check storage backend
    storage_backend = default_storage.__class__.__name__
    print(f"\n✓ Active Storage Backend: {storage_backend}")

def test_database_connection():
    """Test database connection and data retrieval"""
    print("\n" + "="*70)
    print("🔍 DATABASE CONNECTION TEST")
    print("="*70)
    
    try:
        # Test Requisitions
        pr_count = PurchaseRequisition.objects.count()
        print(f"\n✓ Purchase Requisitions: {pr_count} records")
        
        if pr_count > 0:
            latest_pr = PurchaseRequisition.objects.order_by('-created_at').first()
            print(f"  └─ Latest PR: {latest_pr.pr_number} - {latest_pr.title}")
            print(f"     Type: {latest_pr.requisition_type}")
            print(f"     Status: {latest_pr.status}")
            print(f"     Created: {latest_pr.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Test Purchase Orders
        po_count = PurchaseOrder.objects.count()
        print(f"\n✓ Purchase Orders: {po_count} records")
        
        # Test Vendors
        vendor_count = Vendor.objects.count()
        print(f"✓ Vendors: {vendor_count} records")
        
        print(f"\n✅ Database connection is WORKING")
        return True
        
    except Exception as e:
        print(f"\n❌ Database connection FAILED: {str(e)}")
        return False

def test_form_data_creation():
    """Test creating a new requisition to verify form data persistence"""
    print("\n" + "="*70)
    print("📝 FORM DATA PERSISTENCE TEST")
    print("="*70)
    
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Get or create test user
        user, _ = User.objects.get_or_create(
            email='test@radai.local',
            defaults={'is_active': True}
        )
        
        # Create test requisition
        test_pr = PurchaseRequisition.objects.create(
            pr_number=f'TEST-{PurchaseRequisition.objects.count() + 1}',
            requisition_type='general',
            title='Test Requisition - Form Data Check',
            description='Testing form data persistence',
            category='PUMP',
            department='Testing',
            status='draft',
            priority='normal',
            requested_by=user,
            items=[
                {'item': 'Test Item', 'quantity': 1, 'unit': 'ea', 'estimated_price': 100}
            ]
        )
        
        print(f"\n✅ Test Requisition Created Successfully!")
        print(f"   PR Number: {test_pr.pr_number}")
        print(f"   Type: {test_pr.requisition_type}")
        print(f"   ID: {test_pr.id}")
        
        # Verify it was saved
        retrieved = PurchaseRequisition.objects.get(id=test_pr.id)
        print(f"\n✅ Data Retrieved Successfully!")
        print(f"   Retrieved PR: {retrieved.pr_number}")
        print(f"   Match: {'✓ YES' if retrieved.id == test_pr.id else '✗ NO'}")
        
        # Clean up test data
        test_pr.delete()
        print(f"\n🧹 Test data cleaned up")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Form data persistence test FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def generate_summary():
    """Generate summary and recommendations"""
    print("\n" + "="*70)
    print("📋 SUMMARY & RECOMMENDATIONS")
    print("="*70)
    
    db_config = settings.DATABASES['default']
    use_s3 = getattr(settings, 'USE_S3', False)
    s3_ready = getattr(settings, 'S3_READY', False)
    
    is_railway = 'railway' in db_config['HOST'].lower() or 'rlwy' in db_config['HOST'].lower()
    
    if is_railway:
        print("\n✅ Using Railway PostgreSQL (Production)")
        print("   All form data is saved to production database")
    else:
        print("\n⚠️  Using Local PostgreSQL (Development)")
        print("   Form data is saved to local Docker container")
        print("\n💡 To use Railway PostgreSQL:")
        print("   1. Update .env.local with Railway credentials:")
        print("      DB_HOST=<railway-host>.proxy.rlwy.net")
        print("      DB_PORT=<railway-port>")
        print("      DB_NAME=railway")
        print("      DB_USER=postgres")
        print("      DB_PASSWORD=<railway-password>")
        print("   2. Restart containers: docker-compose -f docker-compose.local.yml restart")
    
    if use_s3 and s3_ready:
        print("\n✅ Using AWS S3 (Production)")
        print("   All file uploads go to S3 bucket")
    elif use_s3 and not s3_ready:
        print("\n⚠️  S3 Safety Mode Active")
        print("   Files saved locally until S3_READY=True")
        print("\n💡 To enable AWS S3:")
        print("   1. Set S3_READY=True in .env.local")
        print("   2. Ensure AWS credentials are correct")
    else:
        print("\n💾 Using Local Storage (Development)")
        print("   Files saved to local filesystem")
        print("\n💡 To use AWS S3:")
        print("   1. Update .env.local:")
        print("      USE_S3=True")
        print("      S3_READY=True")
        print("      AWS_ACCESS_KEY_ID=<your-key>")
        print("      AWS_SECRET_ACCESS_KEY=<your-secret>")
        print("   2. Restart containers")

if __name__ == '__main__':
    print("\n🔍 Smart Configuration Checker - Railway PostgreSQL & AWS S3")
    print("Checking data persistence configuration...")
    
    check_database_config()
    check_s3_config()
    
    if test_database_connection():
        test_form_data_creation()
    
    generate_summary()
    
    print("\n" + "="*70)
    print("✨ Configuration check complete!")
    print("="*70 + "\n")
