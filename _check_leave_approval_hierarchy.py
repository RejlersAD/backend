"""
Diagnostic script to check leave approval hierarchy for specific users.
Run: python _check_leave_approval_hierarchy.py (from backend/ directory)
"""
import os
import sys
import django

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile
from apps.payroll.models import LeaveRequest
from django.db.models import Q

User = get_user_model()

def check_reporting_structure():
    """Check the reporting structure for Ahmed, Tanzeem, and Mohamad"""
    
    print("\n" + "="*80)
    print("LEAVE APPROVAL HIERARCHY DIAGNOSTIC")
    print("="*80 + "\n")
    
    # Find the users
    try:
        ahmed = User.objects.get(email="ahmed.aljefri@rejlers.ae")
        print(f"✓ Found Ahmed: {ahmed.get_full_name()} ({ahmed.email})")
        print(f"  - ID: {ahmed.id}")
        print(f"  - is_staff: {ahmed.is_staff}")
        print(f"  - is_superuser: {ahmed.is_superuser}")
    except User.DoesNotExist:
        print("✗ Ahmed Aljefri not found!")
        ahmed = None
    
    try:
        tanzeem = User.objects.get(email="tanzeem.agra@rejlers.ae")
        print(f"\n✓ Found Tanzeem: {tanzeem.get_full_name()} ({tanzeem.email})")
        print(f"  - ID: {tanzeem.id}")
        print(f"  - is_staff: {tanzeem.is_staff}")
        print(f"  - is_superuser: {tanzeem.is_superuser}")
    except User.DoesNotExist:
        print("\n✗ Tanzeem Agra not found!")
        tanzeem = None
    
    try:
        mohamad = User.objects.get(email="moghawanmeh@rejlers.ae")
        print(f"\n✓ Found Mohamad: {mohamad.get_full_name()} ({mohamad.email})")
        print(f"  - ID: {mohamad.id}")
        print(f"  - is_staff: {mohamad.is_staff}")
        print(f"  - is_superuser: {mohamad.is_superuser}")
    except User.DoesNotExist:
        print("\n✗ Mohamad El-Ghawanmeh not found!")
        mohamad = None
    
    if not all([ahmed, tanzeem, mohamad]):
        print("\n⚠ Cannot continue - some users not found")
        return
    
    # Check RBAC profiles
    print("\n" + "-"*80)
    print("RBAC PROFILE RELATIONSHIPS")
    print("-"*80)
    
    if hasattr(ahmed, 'rbac_profile'):
        ahmed_profile = ahmed.rbac_profile
        print(f"\nAhmed's RBAC Profile:")
        print(f"  - Employee ID: {ahmed_profile.employee_id}")
        print(f"  - Department: {ahmed_profile.department}")
        print(f"  - Manager: {ahmed_profile.manager}")
        if ahmed_profile.manager:
            print(f"  - Manager User: {ahmed_profile.manager.user.get_full_name()} ({ahmed_profile.manager.user.email})")
            print(f"  - 🔍 Direct manager is: {ahmed_profile.manager.user.email}")
    else:
        print(f"\n✗ Ahmed has NO RBAC profile!")
    
    if hasattr(tanzeem, 'rbac_profile'):
        tanzeem_profile = tanzeem.rbac_profile
        print(f"\nTanzeem's RBAC Profile:")
        print(f"  - Employee ID: {tanzeem_profile.employee_id}")
        print(f"  - Department: {tanzeem_profile.department}")
        print(f"  - Manager: {tanzeem_profile.manager}")
        if tanzeem_profile.manager:
            print(f"  - Manager User: {tanzeem_profile.manager.user.get_full_name()} ({tanzeem_profile.manager.user.email})")
            print(f"  - 🔍 Direct manager is: {tanzeem_profile.manager.user.email}")
    else:
        print(f"\n✗ Tanzeem has NO RBAC profile!")
    
    if hasattr(mohamad, 'rbac_profile'):
        mohamad_profile = mohamad.rbac_profile
        print(f"\nMohamad's RBAC Profile:")
        print(f"  - Employee ID: {mohamad_profile.employee_id}")
        print(f"  - Department: {mohamad_profile.department}")
        print(f"  - Manager: {mohamad_profile.manager}")
        if mohamad_profile.manager:
            print(f"  - Manager User: {mohamad_profile.manager.user.get_full_name()} ({mohamad_profile.manager.user.email})")
    else:
        print(f"\n✗ Mohamad has NO RBAC profile!")
    
    # Check leave requests
    print("\n" + "-"*80)
    print("LEAVE REQUESTS")
    print("-"*80)
    
    ahmed_requests = LeaveRequest.objects.filter(employee=ahmed).order_by('-created_at')[:5]
    print(f"\nAhmed's Recent Leave Requests: {ahmed_requests.count()} total")
    for req in ahmed_requests:
        print(f"  - ID: {req.id}")
        print(f"    Status: {req.status} ({req.get_status_display()})")
        print(f"    Dates: {req.start_date} to {req.end_date}")
        print(f"    Created: {req.created_at}")
    
    # Simulate what each user sees
    print("\n" + "-"*80)
    print("QUERYSET SIMULATION - What each user sees")
    print("-"*80)
    
    def simulate_queryset(user, user_name):
        print(f"\n{user_name}'s view:")
        print(f"  is_staff: {user.is_staff}")
        
        qs = LeaveRequest.objects.select_related('leave_type', 'employee', 'reviewed_by', 'rm_reviewed_by').all()
        
        if not user.is_staff:
            # Check if HR manager
            is_hr_mgr = False  # Simplified - would need to check roles
            if not is_hr_mgr:
                emp_code = None
                try:
                    emp_code = user.rbac_profile.employee_id or None
                except:
                    pass
                
                own_q = Q(employee=user)
                if emp_code:
                    own_q |= Q(employee_code=emp_code)
                
                # Include requests where the employee's RBAC profile manager = this user
                managed_q = Q(employee__rbac_profile__manager__user=user)
                qs = qs.filter(own_q | managed_q)
        
        print(f"  Total requests visible: {qs.count()}")
        
        # Show sample of requests
        for req in qs.filter(status__in=['PENDING', 'RM_APPROVED'])[:10]:
            print(f"    - {req.employee_name} ({req.status}): {req.start_date} to {req.end_date}")
            if req.employee and hasattr(req.employee, 'rbac_profile'):
                mgr = req.employee.rbac_profile.manager
                if mgr:
                    print(f"      Employee's manager: {mgr.user.get_full_name()}")
    
    simulate_queryset(ahmed, "Ahmed")
    simulate_queryset(tanzeem, "Tanzeem")
    simulate_queryset(mohamad, "Mohamad")
    
    print("\n" + "="*80)
    print("END OF DIAGNOSTIC")
    print("="*80 + "\n")

if __name__ == '__main__':
    check_reporting_structure()
