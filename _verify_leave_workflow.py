"""
Verify Leave Approval Workflow Setup
- Check reporting hierarchy (Ahmed → Tanzeem)
- Check Sanglin's HR Manager role
- Check leave request status
- Test backend filtering for both RM and HR
"""

import os
import django
import sys

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role
from apps.payroll.models import LeaveRequest
from django.db.models import Q

User = get_user_model()

def print_header(text):
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def check_users():
    print_header("1. USER VERIFICATION")
    
    users = {
        'ahmed': 'ahmed.aljefri@rejlers.ae',
        'tanzeem': 'tanzeem.agra@rejlers.ae',
        'sanglin': 'sanglin.samuel@rejlers.ae'
    }
    
    user_objects = {}
    for name, email in users.items():
        try:
            user = User.objects.get(email=email)
            user_objects[name] = user
            print(f"✅ {name.title()}: {user.email} (ID: {user.id})")
        except User.DoesNotExist:
            print(f"❌ {name.title()}: {email} NOT FOUND")
            return None
    
    return user_objects

def check_reporting_hierarchy(users):
    print_header("2. REPORTING HIERARCHY")
    
    ahmed_profile = UserProfile.objects.filter(user=users['ahmed']).first()
    tanzeem_profile = UserProfile.objects.filter(user=users['tanzeem']).first()
    
    if ahmed_profile and ahmed_profile.manager:
        manager = ahmed_profile.manager.user
        print(f"✅ Ahmed's Manager: {manager.email} (Expected: Tanzeem)")
        if manager.id == users['tanzeem'].id:
            print("   ✓ CORRECT - Ahmed reports to Tanzeem")
        else:
            print(f"   ✗ WRONG - Ahmed reports to {manager.email}, not Tanzeem")
    else:
        print("❌ Ahmed has no manager assigned")
    
    # Check Tanzeem's direct reports
    if tanzeem_profile:
        direct_reports = UserProfile.objects.filter(manager=tanzeem_profile)
        print(f"\n✅ Tanzeem's Direct Reports: {direct_reports.count()}")
        for report in direct_reports:
            print(f"   - {report.user.email}")

def check_hr_roles(users):
    print_header("3. HR MANAGER ROLE CHECK")
    
    # Check if Sanglin has HR Manager role
    sanglin_profile = UserProfile.objects.filter(user=users['sanglin']).first()
    
    if sanglin_profile:
        # Check roles
        roles = sanglin_profile.roles.all()
        print(f"\n✅ Sanglin's Roles ({roles.count()}):")
        for role in roles:
            print(f"   - {role.name} (code: {role.code})")
            if 'hr' in role.code.lower() or 'hr' in role.name.lower():
                print("     ✓ HR ROLE DETECTED")
        
        # Check if user is staff/admin
        print(f"\n✅ Sanglin's Admin Status:")
        print(f"   - is_staff: {users['sanglin'].is_staff}")
        print(f"   - is_superuser: {users['sanglin'].is_superuser}")
        
        # Note: Module access check skipped (field may not exist in this version)
    else:
        print("❌ Sanglin has no UserProfile")

def check_leave_requests(users):
    print_header("4. LEAVE REQUESTS")
    
    # Get Ahmed's leave requests (employee is direct FK to User)
    ahmed_requests = LeaveRequest.objects.filter(employee=users['ahmed']).order_by('-created_at')
    
    print(f"\n✅ Ahmed's Leave Requests: {ahmed_requests.count()}")
    for req in ahmed_requests[:5]:  # Show last 5
        print(f"\n   Request #{req.id}:")
        print(f"   - Type: {req.leave_type}")
        print(f"   - Period: {req.start_date} to {req.end_date}")
        print(f"   - Days: {req.days_requested}")
        print(f"   - Status: {req.status}")
        print(f"   - Created: {req.created_at}")
        
        if req.rm_reviewed_by:
            print(f"   - RM Reviewed by: {req.rm_reviewed_by.email}")
            print(f"   - RM Note: {req.rm_note}")
        
        if req.reviewed_by:
            print(f"   - HR Reviewed by: {req.reviewed_by.email}")
            print(f"   - HR Note: {req.reviewer_note}")

def test_backend_filtering(users):
    print_header("5. BACKEND FILTERING TEST")
    
    # Test what Tanzeem (RM) should see
    print("\n📋 TANZEEM (Reporting Manager) View:")
    print("   Query: LeaveRequest.objects.filter(")
    print("          Q(employee__rbac_profile__manager__user=tanzeem)")
    print("          & Q(status__in=['PENDING', 'RM_APPROVED'])")
    print("   )")
    
    tanzeem_requests = LeaveRequest.objects.filter(
        Q(employee__rbac_profile__manager__user=users['tanzeem']),
        Q(status__in=['PENDING', 'RM_APPROVED'])
    )
    
    print(f"\n   Result: {tanzeem_requests.count()} requests")
    for req in tanzeem_requests:
        print(f"   - #{req.id}: {req.employee.user.email} - {req.leave_type} - {req.status}")
    
    # Test what Sanglin (HR) should see
    print("\n📋 SANGLIN (HR Manager) View:")
    print("   Query: LeaveRequest.objects.filter(")
    print("          status='RM_APPROVED'")
    print("   )")
    
    sanglin_requests = LeaveRequest.objects.filter(status='RM_APPROVED')
    
    print(f"\n   Result: {sanglin_requests.count()} requests")
    for req in sanglin_requests:
        print(f"   - #{req.id}: {req.employee.user.email} - {req.leave_type} - {req.status}")
    
    if sanglin_requests.count() == 0:
        print("\n   ℹ️  Note: HR managers only see requests with status='RM_APPROVED'")
        print("             First, Tanzeem must approve Ahmed's request (PENDING → RM_APPROVED)")

def check_workflow_configuration():
    print_header("6. WORKFLOW CONFIGURATION")
    
    print("\n✅ Expected Workflow:")
    print("   Stage 1 (Reporting Manager):")
    print("   - Ahmed submits → Status: PENDING")
    print("   - Tanzeem sees request (Ahmed is his direct report)")
    print("   - Tanzeem clicks 'Approve'")
    print("   - Backend: POST /api/v1/payroll/leave-requests/{id}/rm-approve/")
    print("   - Status changes: PENDING → RM_APPROVED")
    print("   - rm_reviewed_by = Tanzeem, rm_reviewed_at = now()")
    
    print("\n   Stage 2 (HR Manager):")
    print("   - Request now has status: RM_APPROVED")
    print("   - Sanglin sees request (HR manager sees all RM_APPROVED)")
    print("   - Sanglin clicks 'Approve'")
    print("   - Backend: POST /api/v1/payroll/leave-requests/{id}/approve/")
    print("   - Status changes: RM_APPROVED → APPROVED")
    print("   - reviewed_by = Sanglin, reviewed_at = now()")
    print("   - Leave balance updated")

def recommend_actions(users):
    print_header("7. RECOMMENDED ACTIONS")
    
    # Check current state (employee is direct FK to User)
    ahmed_pending = LeaveRequest.objects.filter(
        employee=users['ahmed'],
        status='PENDING'
    ).exists()
    
    ahmed_rm_approved = LeaveRequest.objects.filter(
        employee=users['ahmed'],
        status='RM_APPROVED'
    ).exists()
    
    print("\n📌 Next Steps:")
    
    if ahmed_pending:
        print("\n   ✅ Ahmed has PENDING leave request")
        print("   → Action: Login as Tanzeem → Go to /approvals")
        print("   → Click 'Pending Leave' → Click 'Approve'")
        print("   → This will change status to RM_APPROVED")
    
    if ahmed_rm_approved:
        print("\n   ✅ Ahmed has RM_APPROVED leave request")
        print("   → Action: Login as Sanglin → Go to /approvals")
        print("   → Click 'Pending Leave' → Click 'Approve'")
        print("   → This will change status to APPROVED (FINAL)")
    
    if not ahmed_pending and not ahmed_rm_approved:
        print("\n   ⚠️  No active leave requests found")
        print("   → Action: Login as Ahmed → Submit new leave request")

def main():
    print("\n" + "╔" + "═"*68 + "╗")
    print("║" + " "*15 + "LEAVE APPROVAL WORKFLOW VERIFICATION" + " "*16 + "║")
    print("╚" + "═"*68 + "╝")
    
    users = check_users()
    if not users:
        print("\n❌ FATAL: Required users not found in database")
        return
    
    check_reporting_hierarchy(users)
    check_hr_roles(users)
    check_leave_requests(users)
    test_backend_filtering(users)
    check_workflow_configuration()
    recommend_actions(users)
    
    print("\n" + "="*70)
    print("  VERIFICATION COMPLETE")
    print("="*70 + "\n")

if __name__ == '__main__':
    main()
