"""Quick check of Ahmed's leave requests and reporting manager"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile
from apps.payroll.models import LeaveRequest
from django.db.models import Q

User = get_user_model()

print("\n" + "="*80)
print("LEAVE APPROVAL WORKFLOW - DATABASE CHECK")
print("="*80 + "\n")

# 1. Check Ahmed
try:
    ahmed = User.objects.get(email='ahmed.aljefri@rejlers.ae')
    ahmed_profile = ahmed.rbac_profile
    print("1. AHMED'S PROFILE:")
    print(f"   User ID: {ahmed.id}")
    print(f"   Email: {ahmed.email}")
    print(f"   Name: {ahmed.get_full_name()}")
    print(f"   Employee ID: {ahmed_profile.employee_id}")
    
    if ahmed_profile.manager:
        mgr = ahmed_profile.manager.user
        print(f"   ✅ Reporting Manager: {mgr.get_full_name()} ({mgr.email})")
        print(f"   Manager User ID: {mgr.id}")
    else:
        print("   ❌ NO REPORTING MANAGER SET!")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# 2. Check Tanzeem
try:
    tanzeem = User.objects.get(email='tanzeem.agra@rejlers.ae')
    tanzeem_profile = tanzeem.rbac_profile
    print("2. TANZEEM'S PROFILE:")
    print(f"   User ID: {tanzeem.id}")
    print(f"   Email: {tanzeem.email}")
    print(f"   Name: {tanzeem.get_full_name()}")
    
    reports = UserProfile.objects.filter(manager=tanzeem_profile)
    print(f"   Direct Reports: {reports.count()}")
    for r in reports:
        print(f"      • {r.user.get_full_name()} ({r.user.email})")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# 3. Check Ahmed's leave requests
try:
    ahmed = User.objects.get(email='ahmed.aljefri@rejlers.ae')
    reqs = LeaveRequest.objects.filter(employee=ahmed).select_related('leave_type').order_by('-created_at')
    print(f"3. AHMED'S LEAVE REQUESTS: {reqs.count()} total")
    
    for idx, r in enumerate(reqs[:5], 1):
        print(f"\n   Request #{idx}:")
        print(f"      ID: {str(r.id)[:8]}...")
        print(f"      Status: {r.status} ({r.get_status_display()})")
        print(f"      Type: {r.leave_type.name if r.leave_type else 'N/A'}")
        print(f"      Dates: {r.start_date} to {r.end_date}")
        print(f"      Days: {r.days_requested}")
        print(f"      Created: {r.created_at.strftime('%Y-%m-%d %H:%M')}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print()

# 4. Check what Tanzeem should see (backend filtering simulation)
try:
    tanzeem = User.objects.get(email='tanzeem.agra@rejlers.ae')
    
    qs = LeaveRequest.objects.select_related('leave_type', 'employee').all()
    managed_q = Q(employee__rbac_profile__manager__user=tanzeem)
    filtered = qs.filter(managed_q)
    
    print(f"4. WHAT TANZEEM SHOULD SEE (Backend Filter):")
    print(f"   Total requests for Tanzeem's direct reports: {filtered.count()}")
    
    pending = filtered.filter(status='PENDING')
    print(f"   PENDING requests needing RM approval: {pending.count()}")
    
    for r in pending:
        print(f"\n      • {r.employee_name}")
        print(f"        Type: {r.leave_type.name if r.leave_type else 'N/A'}")
        print(f"        Dates: {r.start_date} to {r.end_date}")
        print(f"        Status: {r.status}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "="*80 + "\n")
