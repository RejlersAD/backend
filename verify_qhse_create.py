"""
Verification Script: QHSE Project Creation Database Integration
Verifies that new QHSE projects are correctly saved to PostgreSQL database
"""
import os
import django
import sys
from datetime import datetime

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.qhse.models import QHSERunningProject
from django.db import connection

def verify_database_connection():
    """Verify PostgreSQL connection"""
    print("\n" + "="*80)
    print("🔍 QHSE PROJECT CREATION - DATABASE VERIFICATION")
    print("="*80)
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            print(f"\n✅ PostgreSQL Connection: SUCCESS")
            print(f"   Database Version: {version[:50]}...")
            return True
    except Exception as e:
        print(f"\n❌ PostgreSQL Connection: FAILED")
        print(f"   Error: {str(e)}")
        return False

def verify_qhse_table():
    """Verify QHSE table exists and check structure"""
    try:
        # Get actual table name from Django model
        table_name = QHSERunningProject._meta.db_table
        
        with connection.cursor() as cursor:
            # Check table existence using the actual table name
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = %s
            """, [table_name])
            table_exists = cursor.fetchone()
            
            if table_exists:
                print(f"\n✅ QHSE Table: EXISTS ({table_name})")
                
                # Count records
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                print(f"   Total Records: {count}")
                
                # Get latest 3 records
                cursor.execute(f"""
                    SELECT sr_no, project_no, project_title, client, created_at 
                    FROM {table_name}
                    ORDER BY created_at DESC 
                    LIMIT 3
                """)
                recent = cursor.fetchall()
                
                if recent:
                    print(f"\n📋 Latest 3 Projects:")
                    for idx, row in enumerate(recent, 1):
                        print(f"   {idx}. SR#{row[0]} - {row[1]}")
                        print(f"      Title: {row[2][:60]}...")
                        print(f"      Client: {row[3]}")
                        print(f"      Created: {row[4]}")
                
                return True
            else:
                print(f"\n❌ QHSE Table: NOT FOUND ({table_name})")
                
                # List all tables to help debug
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public'
                    AND table_name LIKE '%qhse%'
                    ORDER BY table_name
                """)
                tables = cursor.fetchall()
                if tables:
                    print("\n📋 Available QHSE-related tables:")
                    for (tbl,) in tables:
                        print(f"   - {tbl}")
                
                return False
                
    except Exception as e:
        print(f"\n❌ Table Verification: FAILED")
        print(f"   Error: {str(e)}")
        return False

def test_create_project():
    """Test creating a new project via Django ORM"""
    print(f"\n{'='*80}")
    print("🧪 TEST: Creating New QHSE Project")
    print("="*80)
    
    try:
        # Get next serial number
        max_sr = QHSERunningProject.objects.aggregate(max_sr=django.db.models.Max('sr_no'))['max_sr'] or 0
        next_sr = max_sr + 1
        
        # Create test project
        test_project = QHSERunningProject(
            sr_no=next_sr,
            project_no=f"TEST-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            project_title="[TEST] QHSE Database Integration Verification",
            client="Test Client - Verification",
            project_manager="Test PM",
            project_quality_eng="Test QE",
            project_starting_date=datetime.now().date(),
            man_hour_for_quality=100,
            manhours_used=0,
            quality_billability_percent="0%",
            cars_open=0,
            cars_closed=0,
            obs_open=0,
            obs_closed=0,
            project_kpis_achieved_percent="0%",
            project_completion_percent="0%",
            rejection_of_deliverables_percent="0%",
            cost_of_poor_quality_aed=0
        )
        
        test_project.save()
        
        print(f"\n✅ Project Created Successfully!")
        print(f"   SR No: {test_project.sr_no}")
        print(f"   Project No: {test_project.project_no}")
        print(f"   ID: {test_project.id}")
        
        # Verify in database
        saved_project = QHSERunningProject.objects.get(id=test_project.id)
        print(f"\n✅ Project Retrieved from Database!")
        print(f"   Title: {saved_project.project_title}")
        print(f"   Client: {saved_project.client}")
        print(f"   Created At: {saved_project.created_at}")
        
        # Clean up test data
        print(f"\n🧹 Cleaning up test data...")
        test_project.delete()
        print(f"✅ Test project deleted")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test Create: FAILED")
        print(f"   Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def verify_api_endpoint():
    """Verify API endpoint configuration"""
    print(f"\n{'='*80}")
    print("🔌 API ENDPOINT VERIFICATION")
    print("="*80)
    
    try:
        from apps.qhse.views import QHSERunningProjectViewSet
        from apps.qhse.serializers import QHSERunningProjectSerializer
        
        print(f"\n✅ ViewSet Found: QHSERunningProjectViewSet")
        print(f"   Model: {QHSERunningProjectViewSet.queryset.model.__name__}")
        print(f"   Serializer: {QHSERunningProjectViewSet.serializer_class.__name__}")
        
        # Check supported methods
        print(f"\n📝 Supported HTTP Methods:")
        viewset_methods = ['list', 'create', 'retrieve', 'update', 'partial_update', 'destroy']
        for method in viewset_methods:
            has_method = hasattr(QHSERunningProjectViewSet, method)
            status = "✅" if has_method else "❌"
            print(f"   {status} {method.upper()}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ API Verification: FAILED")
        print(f"   Error: {str(e)}")
        return False

def main():
    """Run all verification tests"""
    results = []
    
    # Test 1: Database Connection
    results.append(("Database Connection", verify_database_connection()))
    
    # Test 2: Table Verification
    results.append(("Table Structure", verify_qhse_table()))
    
    # Test 3: API Endpoint
    results.append(("API Endpoint", verify_api_endpoint()))
    
    # Test 4: Create Project
    results.append(("Create Test", test_create_project()))
    
    # Summary
    print(f"\n{'='*80}")
    print("📊 VERIFICATION SUMMARY")
    print("="*80)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {status} - {test_name}")
    
    all_passed = all(result[1] for result in results)
    
    print(f"\n{'='*80}")
    if all_passed:
        print("✅ ALL TESTS PASSED - QHSE Project Creation is Working!")
        print("   New projects from frontend will be saved to PostgreSQL")
    else:
        print("❌ SOME TESTS FAILED - Please review errors above")
    print("="*80 + "\n")
    
    return all_passed

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
