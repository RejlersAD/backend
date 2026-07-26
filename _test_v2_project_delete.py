"""
Test V2 project deletion after creating missing tables
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

# Create a test project
try:
    project = PIDVProject.objects.create(
        project_name="Test Delete Project",
        description="Project to test deletion functionality",
        created_by=admin_user
    )
    print(f"✅ Test project created: {project.id}")
    
    # Try to delete it
    project_id = project.id
    project.delete()
    print(f"✅ Project deleted successfully!")
    
    # Verify it's gone
    exists = PIDVProject.objects.filter(id=project_id).exists()
    if not exists:
        print(f"✅ Verified: Project no longer exists in database")
        print("\n🎯 PROJECT DELETE FUNCTIONALITY WORKING!")
    else:
        print(f"❌ Error: Project still exists after delete")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
