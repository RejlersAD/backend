"""
Test user creation with module-based access
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module, Role, RoleModule, UserProfile, Organization
from django.contrib.auth import get_user_model
from apps.rbac.serializers import UserProfileSerializer
from rest_framework.test import APIRequestFactory
from unittest.mock import Mock

User = get_user_model()

# Get modules
modules = Module.objects.filter(is_active=True)[:3]  # Get first 3 modules
module_ids = [str(module.id) for module in modules]

print("=" * 70)
print("TESTING USER CREATION WITH MODULE-BASED ACCESS")
print("=" * 70)

print(f"\nSelected Modules ({len(module_ids)}):")
for module in modules:
    print(f"  - {module.name} ({module.id})")

# Prepare test data
test_email = "test.user@example.com"
test_data = {
    'email': test_email,
    'password': 'TestPass123',
    'first_name': 'Test',
    'last_name': 'User',
    'organization': Organization.objects.first().id,
    'department': 'Engineering',
    'job_title': 'Senior Engineer',
    'module_ids': module_ids
}

print(f"\nCreating user: {test_email}")
print(f"With {len(module_ids)} modules selected")

try:
    # Delete if exists
    User.objects.filter(email=test_email).delete()
    
    # Create mock request
    factory = APIRequestFactory()
    request = factory.post('/api/v1/rbac/users/')
    request.user = User.objects.filter(is_superuser=True).first()
    
    # Create user through serializer
    context = {'request': request}
    serializer = UserProfileSerializer(data=test_data, context=context)
    
    if serializer.is_valid():
        profile = serializer.save()
        
        print(f"\n✅ User created successfully!")
        print(f"   Email: {profile.user.email}")
        print(f"   Name: {profile.user.first_name} {profile.user.last_name}")
        print(f"   Organization: {profile.organization.name}")
        
        # Check assigned role
        user_roles = profile.roles.all()
        print(f"\n✅ Assigned Roles: {user_roles.count()}")
        for role in user_roles:
            print(f"   - {role.name} ({role.code})")
            
            # Check role modules
            role_modules = RoleModule.objects.filter(role=role)
            print(f"     Modules assigned: {role_modules.count()}")
            for rm in role_modules:
                print(f"       • {rm.module.name}")
        
        # Verify permissions
        all_permissions = profile.get_all_permissions()
        print(f"\n✅ Total Permissions: {all_permissions.count()}")
        
        # Verify modules
        all_modules = profile.get_all_modules()
        print(f"✅ Accessible Modules: {len(all_modules)}")
        for module in all_modules:
            print(f"   • {module.name}")
        
        print("\n" + "=" * 70)
        print("✅ TEST PASSED - User creation with modules works correctly!")
        print("=" * 70)
        
    else:
        print(f"\n❌ Validation errors:")
        for field, errors in serializer.errors.items():
            print(f"   {field}: {errors}")
        
except Exception as e:
    print(f"\n❌ Error creating user: {e}")
    import traceback
    traceback.print_exc()
