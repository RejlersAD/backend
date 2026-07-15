"""
Check Leave Approval Setup - Debug Script
Verifies:
1. Ahmed's reporting manager is set to Tanzeem
2. Leave request exists and has correct status
3. Backend filtering works correctly
"""
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
print("LEAVE APPROVAL WORKFLOW DEBUG")
print("="*80)

# ── 1. Check Ahmed's Profile ────────────────────────────────────────────────
print("\n1️⃣  CHECKING AHMED'S PROFILE...")
try:
    ahmed_user = User.objects.get(email='ahmed.aljefri@rejlers.ae')
    ahmed_profile = UserProfile.objects.get(user=ahmed_user)
    
    print(f"✅ Ahmed found:")
    print(f"   - User ID: {ahmed_user.id}")
    print(f"   - Email: {ahmed_user.email}")
    print(f"   - Name: {ahmed_user.get_full_name()}")
    print(f"   - Employee ID: {ahmed_profile.employee_id}")
    
    if ahmed_profile.manager:
        manager_user = ahmed_profile.manager.user
        print(f"   - Reporting Manager: {manager_user.get_full_name()} ({manager_user.email})")
        print(f"   - Manager User ID: {manager_user.id}")
    else:
        print(f"   ⚠️  NO REPORTING MANAGER SET!")
        
except User.DoesNotExist:
    print("❌ Ahmed not found with email 'ahmed.aljefri@rejlers.ae'")
except UserProfile.DoesNotExist:
    print("❌ Ahmed's UserProfile not found")
except Exception as e:
    print(f"❌ Error: {e}")

# ── 2. Check Tanzeem's Profile ──────────────────────────────────────────────
print("\n2️⃣  CHECKING TANZEEM'S PROFILE...")
try:
    tanzeem_user = User.objects.get(email='tanzeem.agra@rejlers.ae')
    tanzeem_profile = UserProfile.objects.get(user=tanzeem_user)
    
    print(f"✅ Tanzeem found:")
    print(f"   - User ID: {tanzeem_user.id}")
    print(f"   - Email: {tanzeem_user.email}")
    print(f"   - Name: {tanzeem_user.get_full_name()}")
    
    # Check direct reports
    direct_reports = UserProfile.objects.filter(manager=tanzeem_profile)
    print(f"   - Direct Reports: {direct_reports.count()}")
    for report in direct_reports:
        print(f"     • {report.user.get_full_name()} ({report.user.email})")
        
except User.DoesNotExist:
    print("❌ Tanzeem not found with email 'tanzeem.agra@rejlers.ae'")
except UserProfile.DoesNotExist:
    print("❌ Tanzeem's UserProfile not found")
except Exception as e:
    print(f"❌ Error: {e}")

# ── 3. Check Ahmed's Leave Requests ─────────────────────────────────────────
print("\n3️⃣  CHECKING AHMED'S LEAVE REQUESTS...")
try:
    ahmed_requests = LeaveRequest.objects.filter(
        Q(employee=ahmed_user) | Q(employee_code=ahmed_profile.employee_id)
    ).select_related('leave_type', 'employee', 'reviewed_by', 'rm_reviewed_by')
    
    print(f"✅ Found {ahmed_requests.count()} leave request(s)")
    
    for idx, req in enumerate(ahmed_requests.order_by('-created_at')[:5], 1):
        print(f"\n   Request #{idx}:")
        print(f"   - ID: {req.id}")
        print(f"   - Employee: {req.employee_name}")
        print(f"   - Leave Type: {req.leave_type.name if req.leave_type else 'N/A'}")
        print(f"   - Dates: {req.start_date} to {req.end_date}")
        print(f"   - Days: {req.days_requested}")
        print(f"   - Status: {req.status} ({req.get_status_display()})")
        print(f"   - Reason: {req.reason[:50]}..." if len(req.reason) > 50 else f"   - Reason: {req.reason}")
        print(f"   - Created: {req.created_at}")
        
        if req.rm_reviewed_by:
            print(f"   - RM Reviewed By: {req.rm_reviewed_by.get_full_name()}")
            print(f"   - RM Reviewed At: {req.rm_reviewed_at}")
            print(f"   - RM Note: {req.rm_note}")
        
        if req.reviewed_by:
            print(f"   - HR Reviewed By: {req.reviewed_by.get_full_name()}")
            print(f"   - HR Reviewed At: {req.reviewed_at}")
            
except Exception as e:
    print(f"❌ Error fetching leave requests: {e}")

# ── 4. Test Backend Filtering Logic ─────────────────────────────────────────
print("\n4️⃣  TESTING BACKEND FILTERING (What Tanzeem should see)...")
try:
    tanzeem_user = User.objects.get(email='tanzeem.agra@rejlers.ae')
    
    # Simulate the backend queryset filter
    qs = LeaveRequest.objects.select_related('leave_type', 'employee', 'reviewed_by', 'rm_reviewed_by').all()
    
    # Apply the manager filter (from LeaveRequestViewSet.get_queryset)
    managed_q = Q(employee__rbac_profile__manager__user=tanzeem_user)
    filtered_qs = qs.filter(managed_q)
    
    print(f"✅ Tanzeem should see {filtered_qs.count()} leave request(s)")
    
    for idx, req in enumerate(filtered_qs.order_by('-created_at')[:10], 1):
        print(f"\n   Request #{idx}:")
        print(f"   - Employee: {req.employee_name}")
        print(f"   - Status: {req.status}")
        print(f"   - Dates: {req.start_date} to {req.end_date}")
        print(f"   - Created: {req.created_at}")
    
    # Also check pending requests specifically
    pending_qs = filtered_qs.filter(status='PENDING')
    print(f"\n   📌 PENDING requests: {pending_qs.count()}")
    for req in pending_qs:
        print(f"      • {req.employee_name} - {req.leave_type.name if req.leave_type else 'N/A'} - {req.start_date} to {req.end_date}")
        
except Exception as e:
    print(f"❌ Error: {e}")

# ── 5. Check Leave Types ────────────────────────────────────────────────────
print("\n5️⃣  CHECKING LEAVE TYPES...")
from apps.payroll.models import LeaveType
leave_types = LeaveType.objects.filter(is_active=True)
print(f"✅ Found {leave_types.count()} active leave type(s)")
for lt in leave_types[:10]:
    print(f"   - {lt.name} ({lt.code}) - Requires approval: {lt.requires_approval}")

print("\n" + "="*80)
print("DEBUG COMPLETE")
print("="*80 + "\n")
