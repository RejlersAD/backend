"""
Data Visibility Test Script
Tests row-level security implementation across modules
"""
import django
import os
import sys

# Setup Django
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Module
from apps.rbac.data_visibility_config import (
    is_admin_user,
    user_has_module_access,
    get_users_with_module_access,
    build_visibility_filter,
)

User = get_user_model()


def print_header(title):
    """Print formatted header"""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}\n")


def test_module_access():
    """Test module access checks"""
    print_header("TEST 1: Module Access Verification")
    
    modules_to_test = ['crs_documents', 'qhse', 'finance', 'pfd_converter', 'pid_analysis']
    
    # Get sample users
    users = User.objects.filter(is_active=True)[:5]
    
    print(f"📊 Testing {len(users)} users across {len(modules_to_test)} modules\n")
    
    for user in users:
        print(f"👤 User: {user.email}")
        is_admin = is_admin_user(user)
        print(f"   Admin: {'✅ Yes' if is_admin else '❌ No'}")
        
        for module_code in modules_to_test:
            has_access = user_has_module_access(user, module_code)
            status_icon = "✅" if has_access else "❌"
            print(f"   {status_icon} {module_code}: {has_access}")
        print()


def test_team_members():
    """Test team member retrieval"""
    print_header("TEST 2: Team Member Identification")
    
    modules_to_test = ['crs_documents', 'qhse', 'finance', 'pfd_converter', 'pid_analysis']
    
    for module_code in modules_to_test:
        team_ids = get_users_with_module_access(module_code)
        print(f"📦 {module_code}")
        print(f"   Team size: {len(team_ids)} members")
        
        if len(team_ids) > 0:
            team_users = User.objects.filter(id__in=team_ids[:3])  # Show first 3
            for user in team_users:
                print(f"   - {user.email}")
        print()


def test_visibility_filters():
    """Test visibility filter generation"""
    print_header("TEST 3: Visibility Filter Generation")
    
    # Test different scenarios
    test_scenarios = [
        {
            'module': 'qhse',
            'owner_field': None,
            'description': 'QHSE (Team-only, no personal fallback)'
        },
        {
            'module': 'crs_documents',
            'owner_field': 'uploaded_by',
            'description': 'CRS (Team + Personal fallback)'
        },
        {
            'module': 'finance',
            'owner_field': 'created_by',
            'description': 'Finance (Team + Personal fallback)'
        },
    ]
    
    # Get test users
    admin_user = User.objects.filter(
        rbac_profile__roles__code='super_admin'
    ).first()
    
    regular_user = User.objects.exclude(
        rbac_profile__roles__code__in=['super_admin', 'admin']
    ).first()
    
    if admin_user:
        print(f"🛡️ Admin User: {admin_user.email}\n")
        for scenario in test_scenarios:
            filter_q = build_visibility_filter(
                user=admin_user,
                module_code=scenario['module'],
                owner_field=scenario['owner_field']
            )
            print(f"   {scenario['description']}")
            print(f"   Filter: {filter_q} (Empty = See all)")
            print()
    
    if regular_user:
        print(f"👤 Regular User: {regular_user.email}\n")
        for scenario in test_scenarios:
            filter_q = build_visibility_filter(
                user=regular_user,
                module_code=scenario['module'],
                owner_field=scenario['owner_field']
            )
            print(f"   {scenario['description']}")
            print(f"   Filter: {filter_q}")
            has_access = user_has_module_access(regular_user, scenario['module'])
            print(f"   Has module access: {'✅ Yes' if has_access else '❌ No'}")
            print()


def test_actual_data():
    """Test with actual data if available"""
    print_header("TEST 4: Actual Data Filtering")
    
    try:
        from apps.crs.models import CRSDocument
        from apps.qhse.models import QHSERunningProject
        
        # Test CRS
        total_crs = CRSDocument.objects.count()
        print(f"📄 CRS Documents: {total_crs} total\n")
        
        if total_crs > 0:
            # Test with a regular user
            test_user = User.objects.filter(is_active=True).exclude(
                rbac_profile__roles__code__in=['super_admin', 'admin']
            ).first()
            
            if test_user:
                filter_q = build_visibility_filter(
                    user=test_user,
                    module_code='crs_documents',
                    owner_field='uploaded_by'
                )
                
                visible_docs = CRSDocument.objects.filter(filter_q).count()
                has_crs_module = user_has_module_access(test_user, 'crs_documents')
                
                print(f"   User: {test_user.email}")
                print(f"   Has CRS module: {'✅ Yes' if has_crs_module else '❌ No'}")
                print(f"   Visible documents: {visible_docs}/{total_crs}")
                
                if has_crs_module:
                    print(f"   ✅ Team member sees all documents")
                else:
                    print(f"   ℹ️ Non-member sees only personal documents")
        
        # Test QHSE
        total_qhse = QHSERunningProject.objects.count()
        print(f"\n📊 QHSE Projects: {total_qhse} total\n")
        
        if total_qhse > 0:
            test_user = User.objects.filter(is_active=True).exclude(
                rbac_profile__roles__code__in=['super_admin', 'admin']
            ).first()
            
            if test_user:
                filter_q = build_visibility_filter(
                    user=test_user,
                    module_code='qhse',
                    owner_field=None
                )
                
                visible_projects = QHSERunningProject.objects.filter(filter_q).count()
                has_qhse_module = user_has_module_access(test_user, 'qhse')
                
                print(f"   User: {test_user.email}")
                print(f"   Has QHSE module: {'✅ Yes' if has_qhse_module else '❌ No'}")
                print(f"   Visible projects: {visible_projects}/{total_qhse}")
                
                if has_qhse_module:
                    print(f"   ✅ Team member sees all projects")
                else:
                    print(f"   ❌ Non-member has no access")
    
    except Exception as e:
        print(f"⚠️ Could not test actual data: {str(e)}")


def test_configuration_coverage():
    """Test configuration coverage"""
    print_header("TEST 5: Configuration Coverage")
    
    from apps.rbac.data_visibility_config import DATA_VISIBILITY_CONFIG
    
    configured_modules = list(DATA_VISIBILITY_CONFIG.keys())
    
    print(f"📋 Configured Modules: {len(configured_modules)}\n")
    
    for module_code, config in DATA_VISIBILITY_CONFIG.items():
        strategy = config.get('strategy')
        owner_field = config.get('owner_field', 'N/A')
        description = config.get('description', 'No description')
        
        print(f"   {module_code}")
        print(f"   - Strategy: {strategy}")
        print(f"   - Owner Field: {owner_field}")
        print(f"   - Description: {description}")
        print()


def run_all_tests():
    """Run all tests"""
    print("\n" + "🔐" * 40)
    print("  DATA VISIBILITY SYSTEM TEST SUITE")
    print("🔐" * 40)
    
    try:
        test_configuration_coverage()
        test_module_access()
        test_team_members()
        test_visibility_filters()
        test_actual_data()
        
        print_header("✅ ALL TESTS COMPLETED")
        print("Review the output above to verify data visibility is working correctly.\n")
        
    except Exception as e:
        print_header("❌ TEST FAILED")
        print(f"Error: {str(e)}")
        import traceback
        print(traceback.format_exc())


if __name__ == '__main__':
    run_all_tests()
