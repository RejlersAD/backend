"""Verify RadAI managers in local database"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

print("\n=== LOCAL DATABASE VERIFICATION ===\n")
print(f"Total users: {User.objects.count()}")
print(f"Total profiles: {UserProfile.objects.count()}")
print(f"Active profiles: {UserProfile.objects.filter(status='active', is_deleted=False).count()}")

print("\nRadAI Managers:")
for p in UserProfile.objects.filter(department='radai', is_deleted=False):
    print(f"  ✅ {p.user.get_full_name()} ({p.user.email})")
    print(f"     Status: {p.status}, Active: {p.user.is_active}, Org: {p.organization}")

# Test what the API will return
print("\n=== SIMULATING API RESPONSE ===")
first_user = User.objects.filter(is_active=True).first()
if first_user and hasattr(first_user, 'rbac_profile'):
    org = first_user.rbac_profile.organization
    engineers = UserProfile.objects.filter(
        is_deleted=False,
        status='active',
        organization=org
    )
    print(f"\nAPI will return {engineers.count()} engineers for org '{org.name}'")
    print("\nManagers in result:")
    for eng in engineers.filter(department='radai'):
        print(f"  ✅ {eng.user.get_full_name()} - {eng.job_title} ({eng.department})")
