"""
Create a test leave request for Ahmed to test the approval workflow
This will create a PENDING request that Tanzeem can approve
"""

import os
import django
import sys
from datetime import date, timedelta

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.payroll.models import LeaveRequest, LeaveType, LeaveRequestStatus
from decimal import Decimal

User = get_user_model()

def create_test_leave_request():
    print("\n" + "="*70)
    print("  CREATE TEST LEAVE REQUEST FOR AHMED")
    print("="*70)
    
    # Get users
    try:
        ahmed = User.objects.get(email='ahmed.aljefri@rejlers.ae')
        print(f"\n✅ Found Ahmed: {ahmed.email} (ID: {ahmed.id})")
    except User.DoesNotExist:
        print("\n❌ Ahmed not found!")
        return
    
    # Get or create a leave type
    leave_type, created = LeaveType.objects.get_or_create(
        code='AL',
        defaults={
            'name': 'AL — Annual Leave',
            'days_per_year': Decimal('30'),
            'carry_forward_max': Decimal('10'),
            'max_consecutive': Decimal('30'),
            'min_notice_days': 3,
            'requires_document': False,
            'paid': True
        }
    )
    
    if created:
        print(f"✅ Created leave type: {leave_type.name}")
    else:
        print(f"✅ Using existing leave type: {leave_type.name}")
    
    # Create leave request
    start_date = date.today() + timedelta(days=7)  # One week from today
    end_date = start_date + timedelta(days=2)      # 3-day leave
    
    leave_request = LeaveRequest.objects.create(
        employee=ahmed,
        employee_code=f'EMP{ahmed.id:03d}',
        employee_name=f'{ahmed.first_name} {ahmed.last_name}' if ahmed.first_name else ahmed.email.split('@')[0],
        department='Engineering',
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        days_requested=Decimal('3.0'),
        reason='Family vacation - testing workflow',
        status=LeaveRequestStatus.PENDING
    )
    
    print(f"\n✅ CREATED LEAVE REQUEST #{leave_request.id}")
    print(f"   Employee: {leave_request.employee_name}")
    print(f"   Type: {leave_request.leave_type}")
    print(f"   Period: {leave_request.start_date} to {leave_request.end_date}")
    print(f"   Days: {leave_request.days_requested}")
    print(f"   Reason: {leave_request.reason}")
    print(f"   Status: {leave_request.status}")
    
    print("\n" + "="*70)
    print("  WORKFLOW TEST INSTRUCTIONS")
    print("="*70)
    
    print("\n📋 STEP 1: TANZEEM (Reporting Manager) APPROVAL")
    print("   1. Login as: tanzeem.agra@rejlers.ae")
    print("   2. Go to: http://localhost:5173/approvals")
    print("   3. You should see: 'Pending Leave' KPI card with count = 1")
    print("   4. Click the card to see Ahmed's leave request")
    print("   5. Click 'Approve' button")
    print("   6. Confirm the action")
    print("   7. ✅ Status should change: PENDING → RM_APPROVED")
    print("   8. Request disappears from Tanzeem's pending list")
    
    print("\n📋 STEP 2: SANGLIN (HR Manager) FINAL APPROVAL")
    print("   1. Login as: sanglin.samuel@rejlers.ae")
    print("   2. Go to: http://localhost:5173/approvals")
    print("   3. You should see: 'Pending Leave' KPI card with count = 1")
    print("   4. Click the card to see Ahmed's leave request (RM_APPROVED)")
    print("   5. Click 'Approve' button")
    print("   6. Confirm the action")
    print("   7. ✅ Status should change: RM_APPROVED → APPROVED (FINAL)")
    print("   8. Leave balance updated")
    
    print("\n📋 STEP 3: VERIFY ALL DASHBOARDS")
    print("   1. Ahmed's dashboard: Shows APPROVED leave")
    print("   2. Tanzeem's dashboard: Shows RM_APPROVED history")
    print("   3. Sanglin's dashboard: Shows APPROVED history")
    
    print("\n" + "="*70)
    print("  API ENDPOINTS BEING TESTED")
    print("="*70)
    
    print(f"\n✅ Stage 1 (Tanzeem):")
    print(f"   POST /api/v1/payroll/leave-requests/{leave_request.id}/rm-approve/")
    print(f"   Body: {{ \"note\": \"Approved by reporting manager\" }}")
    
    print(f"\n✅ Stage 2 (Sanglin):")
    print(f"   POST /api/v1/payroll/leave-requests/{leave_request.id}/approve/")
    print(f"   Body: {{ \"note\": \"Approved by HR\" }}")
    
    print("\n" + "="*70)
    print("  READY TO TEST!")
    print("="*70 + "\n")

if __name__ == '__main__':
    create_test_leave_request()
