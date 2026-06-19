"""
Payroll Intelligence — Views
==============================
7 viewsets + 1 dashboard summary view.
All endpoints require authentication.
"""
from __future__ import annotations

import datetime
import uuid
import logging

from decimal import Decimal

from django.db.models import Sum, Count, Q, Avg
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.finance.salary_models import (
    PayrollRun, SalarySlip, EmployeeSalaryInfo, SalaryStatus,
)

from .models import (
    PayrollValidationLog,
    PayrollAuditAlert,
    ProjectCostAllocation,
    AIInsightSnapshot,
    ChatbotMessage,
    AlertStatus,
    EmployeeLeaveRecord,
    EmployeeLeaveMonthly,
    LeaveType,
    LeaveRequest,
    LeaveRequestStatus,
    PublicHoliday,
    AttendanceOverride,
    SalaryComponent,
    EmployeeSalaryStructure,
    SalaryStructureStatus,
    SalaryHistory,
    DailyWorkLog,
    DailyWorkLogApprovalStatus,
)
from .serializers import (
    PayrollValidationLogSerializer,
    PayrollAuditAlertSerializer,
    ProjectCostAllocationSerializer,
    AIInsightSnapshotSerializer,
    ChatbotMessageSerializer,
    EmployeeLeaveRecordSerializer,
    EmployeeLeaveRecordListSerializer,
    LeaveTypeSerializer,
    LeaveRequestSerializer,
    PublicHolidaySerializer,
    AttendanceOverrideSerializer,
    SalaryComponentSerializer,
    EmployeeSalaryStructureSerializer,
    SalaryHistorySerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dashboard Summary View
# ─────────────────────────────────────────────────────────────────────────────

class PayrollDashboardSummaryView(APIView):
    """
    Single endpoint that aggregates all KPI data for the Payroll Dashboard.
    Returns a flat dict consumed directly by the frontend KPI tiles.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        current_month = now.month
        current_year  = now.year

        # ── Salary-slip KPIs (current month) ────────────────────────────────
        slip_agg = SalarySlip.objects.filter(
            month=current_month,
            year=current_year,
        ).aggregate(
            total_gross=Sum('gross_salary'),
            total_net=Sum('net_salary'),
            total_deductions=Sum('total_deductions'),
            slip_count=Count('id'),
        )

        # YTD totals
        ytd_agg = SalarySlip.objects.filter(
            year=current_year,
        ).aggregate(ytd_net=Sum('net_salary'))

        pending_approvals = SalarySlip.objects.filter(
            status=SalaryStatus.PENDING_APPROVAL
        ).count()

        # ── Active employee count ────────────────────────────────────────────
        # Primary: EmployeeSalaryInfo (set when salary structures are loaded).
        # Fallback: distinct employee codes from annual leave records.
        salary_employee_count = EmployeeSalaryInfo.objects.filter(is_active=True).count()
        leave_employee_count = (
            EmployeeLeaveRecord.objects
            .filter(year=current_year)
            .exclude(employee_code__isnull=True)
            .values('employee_code')
            .distinct()
            .count()
        )
        total_employees = salary_employee_count or leave_employee_count

        avg_salary = EmployeeSalaryInfo.objects.filter(
            is_active=True
        ).aggregate(avg=Avg('basic_salary'))['avg'] or Decimal('0')

        # ── Open issues ──────────────────────────────────────────────────────
        open_validations = PayrollValidationLog.objects.filter(is_resolved=False).count()
        open_alerts      = PayrollAuditAlert.objects.filter(status=AlertStatus.OPEN).count()

        # Employees with a negative or zero leave balance are an alert
        leave_critical = (
            EmployeeLeaveRecord.objects
            .filter(year=current_year, leave_balance__lte=0)
            .count()
        )

        # ── Leave summary (annual aggregates) ───────────────────────────────
        leave_agg = EmployeeLeaveRecord.objects.filter(year=current_year).aggregate(
            total_taken=Sum('total_taken'),
            total_earned=Sum('total_earned'),
            avg_balance=Avg('leave_balance'),
        )
        leave_employees_taken = (
            EmployeeLeaveRecord.objects.filter(year=current_year, total_taken__gt=0).count()
        )

        # Current-month leave taken (from monthly breakdown table)
        current_month_leave_taken = (
            EmployeeLeaveMonthly.objects
            .filter(record__year=current_year, month=current_month)
            .aggregate(taken=Sum('taken'))['taken'] or Decimal('0')
        )

        # ── Latest payroll run ───────────────────────────────────────────────
        latest_run = PayrollRun.objects.order_by('-year', '-month').first()

        return Response({
            'current_month':       current_month,
            'current_year':        current_year,
            # Employee count — accurate regardless of whether salary runs exist
            'total_employees':         total_employees,
            'salary_employees':        salary_employee_count,
            'leave_employees':         leave_employee_count,
            # Salary-slip KPIs (zero until payroll runs are processed)
            'current_month_gross': str(slip_agg['total_gross'] or 0),
            'current_month_net':   str(slip_agg['total_net'] or 0),
            'total_deductions':    str(slip_agg['total_deductions'] or 0),
            'slip_count':          slip_agg['slip_count'] or 0,
            'pending_approvals':   pending_approvals,
            'ytd_payroll':         str(ytd_agg['ytd_net'] or 0),
            'avg_basic_salary':    str(avg_salary),
            'open_validations':    open_validations,
            'open_alerts':         open_alerts + leave_critical,
            # Leave intelligence — always populated from imported leave data
            'leave_data_available':        leave_employee_count > 0,
            'leave_total_taken_ytd':       str(leave_agg['total_taken'] or 0),
            'leave_total_earned_ytd':      str(leave_agg['total_earned'] or 0),
            'leave_avg_balance':           str(round(leave_agg['avg_balance'] or 0, 2)),
            'leave_employees_taken':       leave_employees_taken,
            'leave_current_month_taken':   str(current_month_leave_taken),
            'leave_critical_alerts':       leave_critical,
            # Activity intelligence — approved daily work logs (month-to-date)
            'approved_activity_hours_mtd':  float(
                DailyWorkLog.objects
                .filter(
                    approval_status=DailyWorkLogApprovalStatus.APPROVED,
                    log_date__year=current_year,
                    log_date__month=current_month,
                )
                .aggregate(h=Sum('hours_spent'))['h'] or 0
            ),
            'approved_activity_count_mtd': DailyWorkLog.objects.filter(
                approval_status=DailyWorkLogApprovalStatus.APPROVED,
                log_date__year=current_year,
                log_date__month=current_month,
            ).count(),
            'latest_run': {
                'id':     str(latest_run.id)     if latest_run else None,
                'code':   latest_run.run_code    if latest_run else None,
                'month':  latest_run.month       if latest_run else None,
                'year':   latest_run.year        if latest_run else None,
                'status': latest_run.status      if latest_run else None,
            },
        })


# ─────────────────────────────────────────────────────────────────────────────
# 2. PayrollValidationLog
# ─────────────────────────────────────────────────────────────────────────────

class PayrollValidationLogViewSet(viewsets.ModelViewSet):
    queryset           = PayrollValidationLog.objects.select_related(
        'payroll_run', 'employee_salary_info__user', 'resolved_by'
    ).all()
    serializer_class   = PayrollValidationLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        run_id = self.request.query_params.get('payroll_run')
        if run_id:
            qs = qs.filter(payroll_run_id=run_id)
        severity = self.request.query_params.get('severity')
        if severity:
            qs = qs.filter(severity=severity)
        is_resolved = self.request.query_params.get('is_resolved')
        if is_resolved is not None:
            qs = qs.filter(is_resolved=is_resolved.lower() == 'true')
        return qs

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        log = self.get_object()
        log.is_resolved = True
        log.resolved_by = request.user
        log.resolved_at = timezone.now()
        log.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
        return Response(self.get_serializer(log).data)

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        """Receive a list of validation findings from the frontend rule engine."""
        items = request.data if isinstance(request.data, list) else request.data.get('items', [])
        created = []
        for item in items:
            ser = self.get_serializer(data=item)
            if ser.is_valid():
                ser.save()
                created.append(ser.data)
        return Response({'created': len(created), 'items': created}, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# 3. PayrollAuditAlert
# ─────────────────────────────────────────────────────────────────────────────

class PayrollAuditAlertViewSet(viewsets.ModelViewSet):
    queryset           = PayrollAuditAlert.objects.select_related(
        'payroll_run', 'compared_to_run', 'employee_salary_info__user', 'acknowledged_by'
    ).all()
    serializer_class   = PayrollAuditAlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        run_id = self.request.query_params.get('payroll_run')
        if run_id:
            qs = qs.filter(payroll_run_id=run_id)
        alert_status = self.request.query_params.get('status')
        if alert_status:
            qs = qs.filter(status=alert_status)
        severity = self.request.query_params.get('severity')
        if severity:
            qs = qs.filter(severity=severity)
        return qs

    @action(detail=True, methods=['post'], url_path='acknowledge')
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.status         = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['status', 'acknowledged_by', 'acknowledged_at'])
        return Response(self.get_serializer(alert).data)

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        alert = self.get_object()
        alert.status = AlertStatus.RESOLVED
        alert.save(update_fields=['status'])
        return Response(self.get_serializer(alert).data)


# ─────────────────────────────────────────────────────────────────────────────
# 4. ProjectCostAllocation
# ─────────────────────────────────────────────────────────────────────────────

class ProjectCostAllocationViewSet(viewsets.ModelViewSet):
    queryset           = ProjectCostAllocation.objects.select_related('salary_slip').all()
    serializer_class   = ProjectCostAllocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        month = self.request.query_params.get('month')
        year  = self.request.query_params.get('year')
        project = self.request.query_params.get('project_code')
        if month:
            qs = qs.filter(month=month)
        if year:
            qs = qs.filter(year=year)
        if project:
            qs = qs.filter(project_code__icontains=project)
        return qs

    @action(detail=False, methods=['get'], url_path='department-summary')
    def department_summary(self, request):
        """Aggregate cost by cost_center for charts."""
        month = request.query_params.get('month')
        year  = request.query_params.get('year')
        qs = self.get_queryset()
        if month:
            qs = qs.filter(month=month)
        if year:
            qs = qs.filter(year=year)
        summary = (
            qs.values('cost_center')
              .annotate(total_cost=Sum('allocated_cost'), total_hours=Sum('allocated_hours'))
              .order_by('-total_cost')
        )
        return Response(list(summary))


# ─────────────────────────────────────────────────────────────────────────────
# 5. AIInsightSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class AIInsightSnapshotViewSet(viewsets.ModelViewSet):
    queryset           = AIInsightSnapshot.objects.select_related('employee_salary_info__user').all()
    serializer_class   = AIInsightSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        emp_id = self.request.query_params.get('employee_salary_info')
        month  = self.request.query_params.get('month')
        year   = self.request.query_params.get('year')
        if emp_id:
            qs = qs.filter(employee_salary_info_id=emp_id)
        if month:
            qs = qs.filter(month=month)
        if year:
            qs = qs.filter(year=year)
        return qs

    @action(detail=False, methods=['delete'], url_path='clear-expired')
    def clear_expired(self, request):
        deleted, _ = AIInsightSnapshot.objects.filter(expires_at__lt=timezone.now()).delete()
        return Response({'deleted': deleted})


# ─────────────────────────────────────────────────────────────────────────────
# 6. ChatbotMessage
# ─────────────────────────────────────────────────────────────────────────────

class ChatbotMessageViewSet(viewsets.ModelViewSet):
    queryset           = ChatbotMessage.objects.select_related('user').all()
    serializer_class   = ChatbotMessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Users can only see their own messages
        qs = super().get_queryset().filter(user=self.request.user)
        session_id = self.request.query_params.get('session_id')
        if session_id:
            qs = qs.filter(session_id=session_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Employee Leave Record ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeLeaveRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API for employee leave records imported from the HR Excel.
    Supports filtering by year, department, employee_code, and name search.
    Detail view includes the full monthly breakdown.
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = EmployeeLeaveRecord.objects.prefetch_related('monthly_breakdown').all()
        year   = self.request.query_params.get('year')
        dept   = self.request.query_params.get('department')
        code   = self.request.query_params.get('employee_code')
        search = self.request.query_params.get('search')
        if year:
            qs = qs.filter(year=year)
        if dept:
            qs = qs.filter(department__iexact=dept)
        if code:
            qs = qs.filter(employee_code=code)
        if search:
            qs = qs.filter(employee_name__icontains=search)
        branch = self.request.query_params.get('branch')
        if branch:
            qs = qs.filter(branch__iexact=branch)
        return qs.order_by('employee_name')

    def get_serializer_class(self):
        # Detail view returns monthly breakdown; list view is lightweight
        if self.action == 'retrieve':
            return EmployeeLeaveRecordSerializer
        return EmployeeLeaveRecordListSerializer


# ─────────────────────────────────────────────────────────────────────────────
# 8. LeaveType — read-only list of leave type codes
# ─────────────────────────────────────────────────────────────────────────────

class LeaveTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only list of active leave types.
    Admins can create/edit types in the Django admin; they appear here immediately.
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = LeaveTypeSerializer
    queryset           = LeaveType.objects.filter(is_active=True)


# ─────────────────────────────────────────────────────────────────────────────
# 9. LeaveRequest — full CRUD with approve / reject / cancel actions
# ─────────────────────────────────────────────────────────────────────────────

class LeaveRequestViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for leave requests.
    POST to create (any auth user / HR on behalf of employee).
    Custom actions: /approve/, /reject/, /cancel/
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = LeaveRequestSerializer

    def get_queryset(self):
        qs = (
            LeaveRequest.objects
            .select_related('leave_type', 'employee', 'reviewed_by')
            .all()
        )
        params = self.request.query_params
        st      = params.get('status')
        code    = params.get('employee_code')
        year    = params.get('year')
        month   = params.get('month')
        search  = params.get('search')
        if st:
            qs = qs.filter(status__iexact=st)
        if code:
            qs = qs.filter(employee_code=code)
        if year and month:
            import datetime as _dt, calendar as _cal
            y, m = int(year), int(month)
            first = _dt.date(y, m, 1)
            last  = _dt.date(y, m, _cal.monthrange(y, m)[1])
            qs = qs.filter(start_date__lte=last, end_date__gte=first)
        elif year:
            qs = qs.filter(start_date__year=int(year))
        if search:
            qs = qs.filter(employee_name__icontains=search)
        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        req = self.get_object()
        if req.status != LeaveRequestStatus.PENDING:
            return Response(
                {'error': f'Cannot approve a {req.status} request'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req.status       = LeaveRequestStatus.APPROVED
        req.reviewed_by  = request.user
        req.reviewed_at  = timezone.now()
        req.reviewer_note = request.data.get('note', '')
        req.save()
        return Response(LeaveRequestSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        req = self.get_object()
        if req.status != LeaveRequestStatus.PENDING:
            return Response(
                {'error': f'Cannot reject a {req.status} request'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req.status       = LeaveRequestStatus.REJECTED
        req.reviewed_by  = request.user
        req.reviewed_at  = timezone.now()
        req.reviewer_note = request.data.get('note', '')
        req.save()
        return Response(LeaveRequestSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        req = self.get_object()
        req.status = LeaveRequestStatus.CANCELLED
        req.save()
        return Response(LeaveRequestSerializer(req).data)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Leave Calendar  — per-employee, per-day approved leave map
#     Consumed by the Summary attendance pivot table to overlay leave codes.
# ─────────────────────────────────────────────────────────────────────────────
# 10. branch_employee_codes — lightweight codes-only endpoint for branch filter
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def branch_employee_codes(request):
    """
    GET /api/v1/payroll/branch-employee-codes/?branch=RIN&year=2026

    Returns a flat list of employee_code values for a given branch (and
    optionally a year).  Used by the frontend Attendance Summary tab to
    filter biometric rows by legal entity without fetching full leave records.

    Response: { "branch": "RIN", "year": 2026, "codes": ["E001", "E002", …] }
    """
    branch = request.GET.get('branch', '').upper().strip()
    year   = request.GET.get('year')

    if not branch:
        return Response({'error': 'branch parameter is required.'}, status=400)

    qs = EmployeeLeaveRecord.objects.filter(branch__iexact=branch)
    if year:
        qs = qs.filter(year=int(year))

    codes = list(qs.values_list('employee_code', flat=True).order_by('employee_code'))
    return Response({'branch': branch, 'year': int(year) if year else None, 'codes': codes})


# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_calendar(request):
    """
    GET /api/v1/payroll/leave-calendar/?year=2026&month=6

    Returns approved leave for all employees in a given month, keyed by
    employee_code → { YYYY-MM-DD: {code, name, color, badge_bg, badge_text, request_id} }.
    Only working days (Mon–Fri) are included.  Used by the Summary attendance
    view to overlay leave codes on the biometric attendance pivot table.
    """
    import calendar as _cal
    import datetime as _dt

    year  = int(request.GET.get('year',  timezone.now().year))
    month = int(request.GET.get('month', timezone.now().month))

    first_day = _dt.date(year, month, 1)
    last_day  = _dt.date(year, month, _cal.monthrange(year, month)[1])

    requests = (
        LeaveRequest.objects
        .select_related('leave_type')
        .filter(
            status=LeaveRequestStatus.APPROVED,
            start_date__lte=last_day,
            end_date__gte=first_day,
        )
    )

    calendar_data: dict = {}
    for req in requests:
        if not req.employee_code:
            continue
        emp = req.employee_code
        if emp not in calendar_data:
            calendar_data[emp] = {}
        # Expand date range, clamped to the requested month
        cur = max(req.start_date, first_day)
        end = min(req.end_date, last_day)
        while cur <= end:
            if cur.weekday() < 5:   # weekdays only (0=Mon…4=Fri)
                calendar_data[emp][cur.isoformat()] = {
                    'code':       req.leave_type.code,
                    'name':       req.leave_type.name,
                    'color':      req.leave_type.color_hex,
                    'badge_bg':   req.leave_type.badge_bg,
                    'badge_text': req.leave_type.badge_text,
                    'request_id': str(req.id),
                }
            cur += _dt.timedelta(days=1)

    return Response({'year': year, 'month': month, 'calendar': calendar_data})


# =============================================================================
# Helper: is_hr_manager
# =============================================================================
def _is_hr_manager(user) -> bool:
    """Return True if the user holds an HR Manager (or admin) role.

    Soft-coded role codes live in apps.rbac -- we look for any role whose code
    starts with 'hr' or equals 'admin'/'superadmin'.  This keeps the check
    forward-compatible: adding a new HR sub-role in the RBAC admin will
    automatically grant access here without code changes.

    Falls back to user.is_staff / user.is_superuser so Django admin accounts
    always retain access even if RBAC is not fully configured.
    """
    if user.is_superuser or user.is_staff:
        return True
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if profile and profile.role:
            code = (profile.role.code or '').lower()
            return code.startswith('hr') or code in ('admin', 'superadmin', 'manager')
    except Exception:
        pass
    return False


# =============================================================================
# 10. PublicHoliday ViewSet
# =============================================================================

class PublicHolidayViewSet(viewsets.ModelViewSet):
    """
    CRUD for public holidays.

    List/Retrieve: any authenticated user (read-only employees can see the calendar).
    Create/Update/Deactivate: HR Manager or admin only.

    Soft-coded filter params:
      ?year=2026          filter by year (default: current year)
      ?region=AE-AZ       filter by region (default: no filter ? return all)
      ?active_only=true   return only is_active=True entries (default: true)
    """
    serializer_class   = PublicHolidaySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        params   = self.request.query_params
        year     = int(params.get('year', datetime.date.today().year))
        region   = params.get('region', None)
        active   = params.get('active_only', 'true').lower() not in ('0', 'false', 'no')

        qs = PublicHoliday.objects.filter(date__year=year)
        if region:
            qs = qs.filter(region=region)
        if active:
            qs = qs.filter(is_active=True)
        return qs.order_by('date')

    def perform_create(self, serializer):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can create public holidays.')
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            source='hr_added',
        )

    def perform_update(self, serializer):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can update public holidays.')
        serializer.save(updated_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """Override destroy to deactivate instead of hard-delete."""
        if not _is_hr_manager(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can deactivate public holidays.')
        obj = self.get_object()
        obj.is_active = False
        obj.updated_by = request.user
        obj.save(update_fields=['is_active', 'updated_by', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# 11. AttendanceOverride ViewSet
# =============================================================================

class AttendanceOverrideViewSet(viewsets.ModelViewSet):
    """
    HR Manager manual corrections for Summary pivot cells.

    List: returns all active overrides for a given month.
        ?year=2026&month=6     filter by year+month (required for Summary tab)
        ?employee_code=E001    filter by specific employee
    Retrieve/Create/Update/Deactivate: HR Manager or admin only.
    Delete is disabled -- overrides are deactivated (is_active=False) instead.

    The frontend applies overrides by building a lookup dict:
        { 'E001': { '2026-06-15': { override_hours: 8.0, reason: '...', ... } } }
    so only ONE override per (employee_code, date) is returned -- the most recent
    active one.  The endpoint returns flat rows; deduplication is in the frontend.
    """
    serializer_class   = AttendanceOverrideSerializer
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']  # no DELETE

    def get_queryset(self):
        params        = self.request.query_params
        year          = params.get('year')
        month         = params.get('month')
        employee_code = params.get('employee_code')

        qs = AttendanceOverride.objects.filter(is_active=True)
        if year and month:
            qs = qs.filter(date__year=int(year), date__month=int(month))
        if employee_code:
            qs = qs.filter(employee_code=employee_code)
        return qs.order_by('employee_code', 'date')

    def _require_hr(self):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can edit attendance overrides.')

    def perform_create(self, serializer):
        self._require_hr()
        # Deactivate any previous override for same (employee_code, date) to
        # ensure only one active record per cell.
        employee_code = serializer.validated_data.get('employee_code')
        date          = serializer.validated_data.get('date')
        if employee_code and date:
            AttendanceOverride.objects.filter(
                employee_code=employee_code,
                date=date,
                is_active=True,
            ).update(is_active=False)
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._require_hr()
        serializer.save()


# =============================================================================
# Salary Management helpers
# =============================================================================

def _is_senior_hr(user) -> bool:
    """True for superuser, staff, or roles with senior-level HR access."""
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    try:
        roles = user.userprofile.roles.all()
        for role in roles:
            code = (role.code or '').lower()
            if code.startswith('senior_hr') or code in ('admin', 'superadmin', 'manager'):
                return True
    except Exception:
        pass
    return False


# =============================================================================
# 10. SalaryComponent
# =============================================================================

class SalaryComponentViewSet(viewsets.ModelViewSet):
    """
    HR Managers can create/update component types.
    Senior HR / Admin can deactivate.
    Read access for all authenticated users.
    """
    serializer_class   = SalaryComponentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = SalaryComponent.objects.all()
        active_only = self.request.query_params.get('active')
        if active_only and active_only.lower() == 'true':
            qs = qs.filter(is_active=True)
        cat = self.request.query_params.get('category')
        if cat:
            qs = qs.filter(category=cat)
        return qs

    def _require_hr(self):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('HR Manager role required.')

    def perform_create(self, serializer):
        self._require_hr()
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._require_hr()
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Soft-delete (deactivate) instead of hard delete."""
        if not _is_senior_hr(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Senior HR role required to deactivate components.')
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# 11. EmployeeSalaryStructure
# =============================================================================

class EmployeeSalaryStructureViewSet(viewsets.ModelViewSet):
    """
    Workflow: DRAFT -> PENDING_APPROVAL -> APPROVED | REJECTED

    Endpoints:
      POST   salary-structures/                   create (HR Manager)
      PATCH  salary-structures/{id}/              update draft
      POST   salary-structures/{id}/submit/       submit for approval
      POST   salary-structures/{id}/approve/      approve (Senior HR)
      POST   salary-structures/{id}/reject/       reject (Senior HR)
      GET    salary-structures/pending/           list pending (Senior HR)
      GET    salary-structures/summary/           one active row per employee
    """
    serializer_class   = EmployeeSalaryStructureSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = EmployeeSalaryStructure.objects.all()
        emp = self.request.query_params.get('employee_code')
        if emp:
            qs = qs.filter(employee_code=emp)
        stat = self.request.query_params.get('status')
        if stat:
            qs = qs.filter(status=stat)
        active = self.request.query_params.get('active')
        if active and active.lower() == 'true':
            qs = qs.filter(is_active=True)
        return qs

    def _require_hr(self):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('HR Manager role required.')

    def _require_senior_hr(self):
        if not _is_senior_hr(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Senior HR role required.')

    def perform_create(self, serializer):
        self._require_hr()
        serializer.save(
            created_by=self.request.user,
            status=SalaryStructureStatus.DRAFT,
        )

    def perform_update(self, serializer):
        self._require_hr()
        obj = self.get_object()
        if obj.status not in (SalaryStructureStatus.DRAFT, SalaryStructureStatus.REJECTED):
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Only DRAFT or REJECTED structures can be edited.')
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Soft-delete (deactivate) — no hard deletes."""
        self._require_hr()
        obj = self.get_object()
        if obj.status == SalaryStructureStatus.APPROVED:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Cannot delete an APPROVED salary structure.')
        obj.is_active = False
        obj.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        """HR Manager submits draft for approval."""
        self._require_hr()
        obj = self.get_object()
        if obj.status != SalaryStructureStatus.DRAFT:
            return Response(
                {'detail': 'Only DRAFT structures can be submitted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj.status       = SalaryStructureStatus.PENDING_APPROVAL
        obj.submitted_by = request.user
        obj.submitted_at = timezone.now()
        obj.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        return Response(EmployeeSalaryStructureSerializer(obj).data)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Senior HR approves a pending structure and writes SalaryHistory."""
        self._require_senior_hr()
        obj = self.get_object()
        if obj.status != SalaryStructureStatus.PENDING_APPROVAL:
            return Response(
                {'detail': 'Only PENDING_APPROVAL structures can be approved.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Deactivate prior active structure
        prev_qs = EmployeeSalaryStructure.objects.filter(
            employee_code=obj.employee_code,
            is_active=True,
            status=SalaryStructureStatus.APPROVED,
        ).exclude(pk=obj.pk)
        prev = prev_qs.first()
        previous_basic = prev.basic_salary if prev else None
        previous_net   = prev.net_salary   if prev else None
        if prev:
            prev.is_active     = False
            prev.superseded_by = obj
            prev.save(update_fields=['is_active', 'superseded_by', 'updated_at'])

        # Approve current
        obj.status      = SalaryStructureStatus.APPROVED
        obj.reviewed_by = request.user
        obj.reviewed_at = timezone.now()
        obj.reviewer_note = request.data.get('reviewer_note', '')
        obj.is_active   = True
        obj.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'reviewer_note',
            'is_active', 'updated_at',
        ])

        # Compute change_percent
        change_pct = None
        if previous_net and previous_net != 0:
            change_pct = round(
                ((obj.net_salary - previous_net) / previous_net) * 100,
                2,
            )

        # Write audit history
        SalaryHistory.objects.create(
            employee_code  = obj.employee_code,
            employee_name  = obj.employee_name,
            change_date    = obj.effective_date,
            previous_basic = previous_basic,
            new_basic      = obj.basic_salary,
            previous_net   = previous_net,
            new_net        = obj.net_salary,
            change_percent = change_pct,
            change_reason  = obj.reviewer_note or 'Salary structure approved',
            structure      = obj,
            approved_by    = request.user,
        )

        return Response(EmployeeSalaryStructureSerializer(obj).data)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Senior HR rejects a pending structure."""
        self._require_senior_hr()
        obj = self.get_object()
        if obj.status != SalaryStructureStatus.PENDING_APPROVAL:
            return Response(
                {'detail': 'Only PENDING_APPROVAL structures can be rejected.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj.status        = SalaryStructureStatus.REJECTED
        obj.reviewed_by   = request.user
        obj.reviewed_at   = timezone.now()
        obj.reviewer_note = request.data.get('reviewer_note', '')
        obj.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'reviewer_note', 'updated_at',
        ])
        return Response(EmployeeSalaryStructureSerializer(obj).data)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending(self, request):
        """Return all structures awaiting approval (Senior HR only)."""
        self._require_senior_hr()
        qs = EmployeeSalaryStructure.objects.filter(
            status=SalaryStructureStatus.PENDING_APPROVAL,
        ).order_by('submitted_at')
        return Response(EmployeeSalaryStructureSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """
        Return one active APPROVED structure per employee (current salary).
        Optionally filter by department.
        """
        qs = EmployeeSalaryStructure.objects.filter(
            is_active=True,
            status=SalaryStructureStatus.APPROVED,
        ).order_by('employee_name')
        dept = request.query_params.get('department')
        if dept:
            qs = qs.filter(department__icontains=dept)
        return Response(EmployeeSalaryStructureSerializer(qs, many=True).data)


# =============================================================================
# 12. SalaryHistory
# =============================================================================

class SalaryHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only. Returns the salary change audit trail.
    Filter by employee_code or date range.
    """
    serializer_class   = SalaryHistorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs  = SalaryHistory.objects.all()
        emp = self.request.query_params.get('employee_code')
        if emp:
            qs = qs.filter(employee_code=emp)
        date_from = self.request.query_params.get('date_from')
        date_to   = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(change_date__gte=date_from)
        if date_to:
            qs = qs.filter(change_date__lte=date_to)
        return qs


# =============================================================================
# Annual Leave Balance Summary
# =============================================================================

def _norm_name(name: str) -> str:
    """Normalise an employee name to a stable lowercase-stripped key for fuzzy match."""
    return ' '.join((name or '').lower().split())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def annual_leave_balance(request):
    """
    GET /api/v1/payroll/annual-leave-balance/?year=2026&month=6

    Returns the YTD leave balance for every employee as of end of *month*.
    The response carries TWO lookup indices so the frontend can match even
    when biometric employee_code format differs from HR Excel codes:

      balances          → keyed by employee_code  (primary lookup)
      balances_by_name  → keyed by normalised lowercase name (fallback)

    Each value:
      { employee_name, joining_date, carryforward,
        earned_ytd, taken_ytd, encashed_ytd, balance,
        annual_entitlement }

    Computation: 22 days/year accrual service formula (pro-rated joining month).
    taken/encashed come from the stored HR-Excel import rows; earned is always
    recomputed from the formula so it stays accurate even if monthly rows are stale.
    """
    from apps.payroll.models import EmployeeLeaveRecord
    from apps.payroll.services.leave_accrual import (
        compute_monthly_earned, compute_running_balance, _dec, ANNUAL_LEAVE_DAYS,
    )
    from datetime import date as _date

    year  = int(request.GET.get('year',  timezone.now().year))
    month = int(request.GET.get('month', timezone.now().month))

    qs = (
        EmployeeLeaveRecord.objects
        .prefetch_related('monthly_breakdown')
        .filter(year=year)
    )
    branch = request.GET.get('branch')
    if branch:
        qs = qs.filter(branch__iexact=branch)

    today  = _date.today()
    result_by_code = {}
    result_by_name = {}

    for rec in qs:
        # Build monthly taken/encashed from stored import rows (HR source of truth)
        stored = {
            r.month: r
            for r in rec.monthly_breakdown.all()
        }

        # Always recompute earned from formula; use stored taken/encashed
        monthly_rows = []
        for m in range(1, month + 1):
            earned   = compute_monthly_earned(
                rec.joining_date, year, m,
                rec.annual_entitlement or ANNUAL_LEAVE_DAYS, today,
            )
            row = stored.get(m)
            taken    = _dec(row.taken    if row else 0)
            encashed = _dec(row.encashed if row else 0)
            monthly_rows.append({'month': m, 'earned': earned, 'taken': taken, 'encashed': encashed})

        balances   = compute_running_balance(_dec(rec.carryforward), monthly_rows, up_to_month=month)
        balance_at = float(balances.get(month, 0))
        earned_ytd   = round(sum(float(r['earned'])   for r in monthly_rows), 4)
        taken_ytd    = round(sum(float(r['taken'])    for r in monthly_rows), 4)
        encashed_ytd = round(sum(float(r['encashed']) for r in monthly_rows), 4)

        payload = {
            'employee_name':      rec.employee_name,
            'joining_date':       rec.joining_date.isoformat() if rec.joining_date else None,
            'carryforward':       float(rec.carryforward),
            'earned_ytd':         earned_ytd,
            'taken_ytd':          taken_ytd,
            'encashed_ytd':       encashed_ytd,
            'balance':            round(balance_at, 4),
            'annual_entitlement': int(rec.annual_entitlement or ANNUAL_LEAVE_DAYS),
        }

        # Primary key: employee_code (string)
        if rec.employee_code:
            result_by_code[str(rec.employee_code).strip()] = payload

        # Fallback key: normalised name (handles code-format mismatches)
        norm = _norm_name(rec.employee_name)
        if norm:
            result_by_name[norm] = payload

    return Response({
        'year':             year,
        'month':            month,
        'balances':         result_by_code,    # keyed by employee_code
        'balances_by_name': result_by_name,    # keyed by normalised name
    })


# =============================================================================
# HR Admin: Upload leave Excel + trigger import+compute (one-shot seed)
# =============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_leave_data(request):
    """
    POST /api/v1/payroll/sync-leave-data/

    HR Manager only.  Accepts a multipart Excel upload (field: file) and:
      1. Saves it to a temp path
      2. Runs import_leave_excel  (upsert all EmployeeLeaveRecord rows)
      3. Runs compute_leave_accrual  (recompute earned + balance)

    Returns a summary dict:  { imported, updated, errors, computed, year }

    Allows seeding / refreshing production from the HR Manager's browser
    without needing Railway CLI access.
    """
    import os, tempfile, warnings
    from decimal import Decimal, InvalidOperation
    from apps.payroll.models import EmployeeLeaveRecord, EmployeeLeaveMonthly
    from apps.payroll.management.commands.import_leave_excel import (
        LEAVE_EXCEL_MAP, BRANCH_OVERRIDES, _to_dec, _to_str,
        _clean_emp_code, _clean_title,
    )
    from apps.payroll.services.leave_accrual import compute_accrual_for_record
    from django.db import transaction

    if not _is_hr_manager(request.user):
        raise PermissionDenied('HR Manager role required to sync leave data.')

    upload = request.FILES.get('file')
    if not upload:
        return Response({'error': 'No file uploaded. POST with field name "file".'}, status=400)

    year   = int(request.data.get('year',   timezone.now().year))
    branch = (request.data.get('branch') or 'RAD').upper().strip()

    # Soft-coded column map for RAD branch
    override = BRANCH_OVERRIDES.get(branch, {})
    em   = {**LEAVE_EXCEL_MAP, **{k: v for k, v in override.items() if k not in ('default_path',)}}
    src  = override.get('source_tag', LEAVE_EXCEL_MAP['source_tag'])
    mmap = em['month_map']

    try:
        import openpyxl
    except ImportError:
        return Response({'error': 'openpyxl not installed on this server.'}, status=500)

    # Save upload to a temp file
    suffix = os.path.splitext(upload.name)[1] or '.xlsx'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.close()

        warnings.filterwarnings('ignore')
        wb = openpyxl.load_workbook(tmp.name, data_only=True)
        sheets = wb.sheetnames

        created = updated = skipped = import_errors = 0

        for sheet_name in sheets:
            ws = wb[sheet_name]
            raw_name    = ws.cell(*em['name_cell']).value
            raw_emp_no  = ws.cell(*em['emp_no_cell']).value
            raw_dept    = ws.cell(*em['dept_cell']).value
            raw_title   = ws.cell(*em['title_cell']).value
            raw_joining = ws.cell(*em['joining_cell']).value

            name = _to_str(raw_name)
            if not name or name.lower() in ('name', 'employee', ''):
                skipped += 1
                continue

            emp_code    = _clean_emp_code(raw_emp_no)
            job_title   = _clean_title(raw_emp_no, raw_title)
            department  = _to_str(raw_dept) or None
            joining_date = raw_joining.date() if hasattr(raw_joining, 'date') else None

            tr  = em['total_row']
            cfr = em['carryforward_row']
            total_earned   = _to_dec(ws.cell(tr, em['col_earned']).value)
            total_taken    = _to_dec(ws.cell(tr, em['col_taken']).value)
            total_encashed = _to_dec(ws.cell(tr, em['col_encashed']).value)
            leave_balance  = _to_dec(ws.cell(tr, em['col_balance']).value)
            carryforward   = _to_dec(ws.cell(cfr, em['col_balance']).value)

            try:
                with transaction.atomic():
                    defaults = dict(
                        employee_name=name, department=department,
                        job_title=job_title or None, joining_date=joining_date,
                        year=year, branch=branch,
                        total_earned=total_earned, total_taken=total_taken,
                        total_encashed=total_encashed, leave_balance=leave_balance,
                        carryforward=carryforward, source_file=src,
                    )
                    if emp_code:
                        rec, was_created = EmployeeLeaveRecord.objects.update_or_create(
                            employee_code=emp_code, year=year, defaults=defaults,
                        )
                    else:
                        rec, was_created = EmployeeLeaveRecord.objects.update_or_create(
                            employee_name=name, year=year,
                            defaults={**defaults, 'employee_code': None},
                        )
                    if was_created:
                        created += 1
                    else:
                        updated += 1

                    for row_idx in range(em['month_start_row'], em['month_end_row'] + 1):
                        month_label = _to_str(ws.cell(row_idx, em['col_month']).value).lower()
                        month_num   = mmap.get(month_label)
                        if month_num is None:
                            continue
                        EmployeeLeaveMonthly.objects.update_or_create(
                            record=rec, month=month_num,
                            defaults=dict(
                                earned   = _to_dec(ws.cell(row_idx, em['col_earned']).value),
                                taken    = _to_dec(ws.cell(row_idx, em['col_taken']).value),
                                encashed = _to_dec(ws.cell(row_idx, em['col_encashed']).value),
                                balance  = _to_dec(ws.cell(row_idx, em['col_balance']).value),
                            ),
                        )
            except Exception as exc:
                import_errors += 1

        # Now recompute accruals for all imported records
        recs = (
            EmployeeLeaveRecord.objects
            .prefetch_related('monthly_breakdown')
            .filter(year=year, branch__iexact=branch)
        )
        computed = computed_errors = 0
        for rec in recs:
            try:
                compute_accrual_for_record(rec, target_year=year, dry_run=False)
                computed += 1
            except Exception:
                computed_errors += 1

    finally:
        os.unlink(tmp.name)

    return Response({
        'year':            year,
        'branch':          branch,
        'sheets_found':    len(sheets),
        'created':         created,
        'updated':         updated,
        'skipped':         skipped,
        'import_errors':   import_errors,
        'computed':        computed,
        'compute_errors':  computed_errors,
    })


# =============================================================================
# DailyWorkLog ViewSet
# =============================================================================
from .models import DailyWorkLog, DailyWorkLogStatus, DailyWorkLogApprovalStatus  # noqa: E402
from .serializers import DailyWorkLogSerializer  # noqa: E402


class DailyWorkLogViewSet(viewsets.ModelViewSet):
    """
    CRUD for personal daily work logs.

    Default scope: the requesting user''s own logs.
    Staff overrides:
      * ?all=true          -> all users'' logs (staff only)
      * ?user_id=<uuid>    -> specific user''s logs (staff only)

    Date filters (compatible with above scopes):
      * ?date=YYYY-MM-DD
      * ?from=YYYY-MM-DD&to=YYYY-MM-DD
      * ?status=in_progress|done|blocked|deferred

    Custom actions:
      GET  /daily-logs/summary/        -> daily totals (hours + task count) per date
      GET  /daily-logs/export-to-s3/   -> export filtered logs as JSON to S3
      GET  /daily-logs/team/           -> latest log per user for a date (staff only)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = DailyWorkLogSerializer
    http_method_names  = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user   = self.request.user
        params = self.request.query_params
        qs     = DailyWorkLog.objects.select_related('user')

        # Scope
        if user.is_staff and params.get('all') == 'true':
            pass  # no user filter — all logs
        elif user.is_staff and params.get('user_id'):
            qs = qs.filter(user_id=params.get('user_id'))
        else:
            qs = qs.filter(user=user)

        # Date filters
        exact_date = params.get('date')
        from_date  = params.get('from')
        to_date    = params.get('to')
        if exact_date:
            qs = qs.filter(log_date=exact_date)
        else:
            if from_date:
                qs = qs.filter(log_date__gte=from_date)
            if to_date:
                qs = qs.filter(log_date__lte=to_date)

        # Status filter
        status_filter = params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        # Approval status filter
        approval_filter = params.get('approval_status')
        if approval_filter:
            qs = qs.filter(approval_status=approval_filter)

        return qs.order_by('-log_date', '-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    # ── Permission helper ──────────────────────────────────────────────────
    @staticmethod
    def _can_approve(request_user, log_obj):
        """
        Returns True if request_user may approve/reject log_obj.
        Rules (OR):
          1. request_user.is_staff or is_superuser
          2. request_user is the direct manager of log_obj.user
             (UserProfile.manager FK points to a UserProfile; check if that
              UserProfile.user == request_user)
        """
        if request_user.is_staff or request_user.is_superuser:
            return True
        try:
            from apps.rbac.models import UserProfile  # noqa: F811
            return UserProfile.objects.filter(
                user=log_obj.user,
                manager__user=request_user,
                is_deleted=False,
            ).exists()
        except Exception:
            return False

    # ── Approve ───────────────────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        log = self.get_object()
        if not self._can_approve(request.user, log):
            return Response(
                {'error': 'You do not have permission to approve this activity.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if log.approval_status == DailyWorkLogApprovalStatus.APPROVED:
            return Response(
                {'error': 'Activity is already approved.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        note = request.data.get('note', '')
        log.approval_status = DailyWorkLogApprovalStatus.APPROVED
        log.approved_by     = request.user
        log.approved_at     = timezone.now()
        log.approval_note   = note
        log.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note'])

        # ── Auto-create ProjectCostAllocation if a SalarySlip exists ───
        try:
            from apps.finance.salary_models import SalarySlip as _Slip  # noqa
            slip = _Slip.objects.filter(
                month=log.log_date.month,
                year=log.log_date.year,
            ).filter(
                # Match by user → EmployeeSalaryInfo → SalarySlip
                employee_salary_info__user=log.user,
            ).first()
            if slip:
                ProjectCostAllocation.objects.create(
                    salary_slip=slip,
                    project_code=log.project_category or 'DAILY-ACTIVITY',
                    project_name=log.project_category or 'Daily Activity',
                    allocated_hours=log.hours_spent,
                    allocation_percent=Decimal('0'),
                    allocated_cost=Decimal('0'),
                    month=log.log_date.month,
                    year=log.log_date.year,
                )
        except Exception:
            pass  # Never block the approval if cost allocation fails

        # ── Notify employee by email ───────────────────────────────────
        try:
            from django.core.mail import send_mail
            from django.conf import settings as _s
            role_label = (
                'Project Manager' if log.submitted_to_role == 'project_manager'
                else 'Reporting Manager' if log.submitted_to_role == 'reporting_manager'
                else 'Manager'
            )
            send_mail(
                subject=f'[RAD AI] Your activity log has been approved',
                message=(
                    f'Hi {log.user.first_name or log.user.email},\n\n'
                    f'Your activity "{log.task_title}" on {log.log_date} '
                    f'({log.hours_spent} hrs) has been approved by your {role_label}'
                    f'{" with note: " + note if note else "."}'
                    f'\n\nApproved by: {request.user.get_full_name() or request.user.email}\n'
                ),
                from_email=getattr(_s, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[log.user.email],
                fail_silently=True,
            )
        except Exception:
            pass

        return Response(DailyWorkLogSerializer(log).data)

    # ── Reject ────────────────────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        log = self.get_object()
        if not self._can_approve(request.user, log):
            return Response(
                {'error': 'You do not have permission to reject this activity.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if log.approval_status == DailyWorkLogApprovalStatus.REJECTED:
            return Response(
                {'error': 'Activity is already rejected.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        note = request.data.get('note', '')
        log.approval_status = DailyWorkLogApprovalStatus.REJECTED
        log.approved_by     = request.user
        log.approved_at     = timezone.now()
        log.approval_note   = note
        log.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note'])

        # ── Notify employee by email ───────────────────────────────────
        try:
            from django.core.mail import send_mail
            from django.conf import settings as _s
            role_label_r = (
                'Project Manager' if log.submitted_to_role == 'project_manager'
                else 'Reporting Manager' if log.submitted_to_role == 'reporting_manager'
                else 'Manager'
            )
            send_mail(
                subject=f'[RAD AI] Your activity log requires revision',
                message=(
                    f'Hi {log.user.first_name or log.user.email},\n\n'
                    f'Your activity "{log.task_title}" on {log.log_date} '
                    f'({log.hours_spent} hrs) was reviewed by your {role_label_r} and was not approved.\n\n'
                    f'Reason: {note or "No reason provided."}\n\n'
                    f'Please update the entry and resubmit for approval.\n\n'
                    f'Reviewed by: {request.user.get_full_name() or request.user.email}\n'
                ),
                from_email=getattr(_s, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[log.user.email],
                fail_silently=True,
            )
        except Exception:
            pass

        return Response(DailyWorkLogSerializer(log).data)

    # -- Summary: daily totals for heatmap + bar chart ---------------------
    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        qs = self.get_queryset()
        from django.db.models import Sum, Count  # noqa: F811
        rows = (
            qs
            .values('log_date')
            .annotate(total_hours=Sum('hours_spent'), task_count=Count('id'))
            .order_by('log_date')
        )
        data = [
            {
                'date':        str(r['log_date']),
                'total_hours': float(r['total_hours'] or 0),
                'task_count':  r['task_count'],
            }
            for r in rows
        ]
        return Response(data)

    # ── Export to S3 ───────────────────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='export-to-s3')
    def export_to_s3(self, request):
        from django.conf import settings as _settings  # noqa: F811
        bucket = getattr(_settings, 'AWS_STORAGE_BUCKET_NAME', '')
        if not bucket:
            return Response(
                {'error': 'S3 storage is not configured on this environment.'},
                status=503,
            )

        import boto3, json, tempfile  # noqa: E402
        from datetime import datetime as _dt  # noqa: F811

        qs   = self.get_queryset()
        data = DailyWorkLogSerializer(qs, many=True).data

        user       = request.user
        now        = _dt.utcnow()
        s3_key     = (
            f'daily-tracker/{user.id}/{now.year:04d}/{now.month:02d}/'
            f'export_{now.strftime("%Y%m%d_%H%M%S")}.json'
        )

        payload = json.dumps(list(data), default=str, ensure_ascii=False)

        region   = getattr(_settings, 'AWS_S3_REGION_NAME', 'us-east-1')
        endpoint = getattr(_settings, 'AWS_S3_ENDPOINT_URL', None)
        s3 = boto3.client(
            's3',
            region_name=region,
            endpoint_url=endpoint,
        )
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=payload.encode('utf-8'),
            ContentType='application/json',
        )

        # Mark exported logs with their S3 key
        qs.update(s3_export_key=s3_key)

        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': s3_key},
            ExpiresIn=3600,
        )

        return Response({'s3_key': s3_key, 'url': presigned_url, 'count': len(data)})

    # ── Team view: latest entries per user for a given date ────────────────
    @action(detail=False, methods=['get'], url_path='team')
    def team(self, request):
        if not request.user.is_staff:
            return Response({'error': 'Staff access required.'}, status=403)

        params = request.query_params
        date   = params.get('date', str(datetime.date.today()))

        from django.db.models import Max  # noqa: F811
        # Subquery: most recent entry per user on target date
        latest = (
            DailyWorkLog.objects
            .filter(log_date=date)
            .values('user')
            .annotate(latest=Max('created_at'))
            .values_list('latest', flat=True)
        )
        logs = (
            DailyWorkLog.objects
            .filter(log_date=date, created_at__in=latest)
            .select_related('user')
            .order_by('user__first_name', 'user__last_name')
        )
        return Response(DailyWorkLogSerializer(logs, many=True).data)
