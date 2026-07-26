"""
Test V2 project creation to verify extraction_status default value fix
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pid_verification_v2.models import PIDVProject
from django.contrib.auth import get_user_model

User = get_user_model()

# Get first superuser
admin_user = User.objects.filter(is_superuser=True).first()

if not admin_user:
    print("❌ No superuser found")
    exit(1)

print(f"✅ Using user: {admin_user.email}")

# Try to create a test project
try:
    project = PIDVProject.objects.create(
        project_name="Test V2 Project - Automated",
        description="Test project to verify extraction_status default",
        created_by=admin_user
    )
    print(f"✅ Project created successfully!")
    print(f"   - ID: {project.id}")
    print(f"   - Name: {project.project_name}")
    print(f"   - Extraction Status: '{project.extraction_status}'")
    print(f"   - Created At: {project.created_at}")
    
    # Clean up - delete the test project
    project.delete()
    print(f"✅ Test project deleted successfully")
    print("\n✅ V2 PROJECT CREATION WORKING CORRECTLY!")
    
except Exception as e:
    print(f"❌ Error creating project: {e}")
    import traceback
    traceback.print_exc()
