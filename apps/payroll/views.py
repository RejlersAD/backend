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
    LeaveType,
    LeaveRequest,
    LeaveRequestStatus,
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

        # Current month gross & net
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

        total_employees = EmployeeSalaryInfo.objects.filter(is_active=True).count()

        avg_salary = EmployeeSalaryInfo.objects.filter(
            is_active=True
        ).aggregate(avg=Avg('basic_salary'))['avg'] or Decimal('0')

        # Open validation issues
        open_validations = PayrollValidationLog.objects.filter(is_resolved=False).count()
        open_alerts      = PayrollAuditAlert.objects.filter(status=AlertStatus.OPEN).count()

        # Latest payroll run
        latest_run = PayrollRun.objects.order_by('-year', '-month').first()

        return Response({
            'current_month':       current_month,
            'current_year':        current_year,
            'total_employees':     total_employees,
            'current_month_gross': str(slip_agg['total_gross'] or 0),
            'current_month_net':   str(slip_agg['total_net'] or 0),
            'total_deductions':    str(slip_agg['total_deductions'] or 0),
            'slip_count':          slip_agg['slip_count'] or 0,
            'pending_approvals':   pending_approvals,
            'ytd_payroll':         str(ytd_agg['ytd_net'] or 0),
            'avg_basic_salary':    str(avg_salary),
            'open_validations':    open_validations,
            'open_alerts':         open_alerts,
            'latest_run':          {
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
