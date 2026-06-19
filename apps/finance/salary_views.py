"""
Salary Slip Automation System - Views
REST API endpoints for payroll management
SOFT-CODED for easy customization and extensibility
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Sum, Count
from django.utils import timezone
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from .salary_models import (
    EmployeeSalaryInfo,
    SalaryComponent,
    EmployeeSalaryComponent,
    PayrollRun,
    SalarySlip,
    SalarySlipApproval,
    SalarySlipEmail,
    SalarySlipAuditLog,
    SalaryStatus,
    ApprovalStatus,
    EmailStatus,
)
from .salary_serializers import (
    EmployeeSalaryInfoSerializer,
    EmployeeSalaryInfoListSerializer,
    SalaryComponentSerializer,
    EmployeeSalaryComponentSerializer,
    PayrollRunSerializer,
    PayrollRunListSerializer,
    SalarySlipSerializer,
    SalarySlipListSerializer,
    SalarySlipCreateSerializer,
    SalarySlipApprovalSerializer,
    ApprovalDecisionSerializer,
    SalarySlipEmailSerializer,
    SendSalarySlipEmailSerializer,
    SalarySlipAuditLogSerializer,
    SalarySlipStatsSerializer,
    PayrollSummarySerializer,
)

logger = logging.getLogger(__name__)


# ===========================
# EMPLOYEE SALARY INFO VIEWSET
# ===========================

class EmployeeSalaryInfoViewSet(viewsets.ModelViewSet):
    """
    API endpoint for employee salary information management
    CRUD operations for employee payroll data
    """
    queryset = EmployeeSalaryInfo.objects.select_related('user').all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return EmployeeSalaryInfoListSerializer
        return EmployeeSalaryInfoSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Filter by user UUID — used by the ESS portal (?employee=<User UUID>)
        employee_uid = self.request.query_params.get('employee')
        if employee_uid:
            queryset = queryset.filter(user_id=employee_uid)

        # Search by employee ID or name
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_id__icontains=search) |
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search) |
                Q(user__email__icontains=search)
            )
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['get'])
    def salary_history(self, request, pk=None):
        """Get salary history for an employee"""
        employee = self.get_object()
        slips = SalarySlip.objects.filter(
            employee_salary_info=employee
        ).order_by('-year', '-month')
        
        serializer = SalarySlipListSerializer(slips, many=True)
        return Response(serializer.data)


# ===========================
# SALARY COMPONENT VIEWSET
# ===========================

class SalaryComponentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for salary components (allowances/deductions)
    Manage component master data
    """
    queryset = SalaryComponent.objects.all()
    serializer_class = SalaryComponentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by component type
        component_type = self.request.query_params.get('component_type')
        if component_type:
            queryset = queryset.filter(component_type=component_type)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset


# ===========================
# EMPLOYEE SALARY COMPONENT VIEWSET
# ===========================

class EmployeeSalaryComponentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for employee-specific salary components
    Manage individual employee allowances/deductions
    """
    queryset = EmployeeSalaryComponent.objects.select_related(
        'employee_salary_info', 'component'
    ).all()
    serializer_class = EmployeeSalaryComponentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by employee
        employee_id = self.request.query_params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(employee_salary_info__id=employee_id)
        
        return queryset


# ===========================
# PAYROLL RUN VIEWSET
# ===========================

class PayrollRunViewSet(viewsets.ModelViewSet):
    """
    API endpoint for payroll run management
    Create and manage monthly payroll cycles
    """
    queryset = PayrollRun.objects.all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return PayrollRunListSerializer
        return PayrollRunSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        # Filter by year
        year = self.request.query_params.get('year')
        if year:
            queryset = queryset.filter(year=year)
        
        return queryset
    
    def perform_create(self, serializer):
        # Generate unique run code
        instance = serializer.save(created_by=self.request.user)
        if not instance.run_code:
            run_code = f"PAY-{instance.year}-{instance.month:02d}"
            instance.run_code = run_code
            instance.save()
    
    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """Process payroll run - generate salary slips for all employees"""
        payroll_run = self.get_object()
        
        if payroll_run.status != 'draft':
            return Response(
                {'error': 'Only draft payroll runs can be processed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Mark as processing
        payroll_run.status = 'processing'
        payroll_run.processing_started_at = timezone.now()
        payroll_run.save()
        
        try:
            # Get all active employees
            employees = EmployeeSalaryInfo.objects.filter(is_active=True)
            payroll_run.total_employees = employees.count()
            payroll_run.save()
            
            # Generate salary slips using service
            from .salary_service import SalaryCalculationService
            service = SalaryCalculationService()
            
            for employee in employees:
                try:
                    service.generate_salary_slip(
                        employee=employee,
                        payroll_run=payroll_run,
                        month=payroll_run.month,
                        year=payroll_run.year,
                        generated_by=request.user
                    )
                    payroll_run.processed_employees += 1
                    payroll_run.save()
                except Exception as e:
                    logger.error(f"Error generating slip for {employee.employee_id}: {str(e)}")
                    payroll_run.error_log += f"\nEmployee {employee.employee_id}: {str(e)}"
                    payroll_run.save()
            
            # Calculate totals
            slips = SalarySlip.objects.filter(payroll_run=payroll_run)
            totals = slips.aggregate(
                total_gross=Sum('gross_salary'),
                total_deductions=Sum('total_deductions'),
                total_net=Sum('net_salary')
            )
            
            payroll_run.total_gross_salary = totals['total_gross'] or Decimal('0.00')
            payroll_run.total_deductions = totals['total_deductions'] or Decimal('0.00')
            payroll_run.total_net_salary = totals['total_net'] or Decimal('0.00')
            payroll_run.status = 'completed'
            payroll_run.processing_completed_at = timezone.now()
            payroll_run.save()
            
            return Response({
                'message': 'Payroll processed successfully',
                'total_employees': payroll_run.total_employees,
                'processed_employees': payroll_run.processed_employees,
                'total_net_salary': str(payroll_run.total_net_salary)
            })
            
        except Exception as e:
            payroll_run.status = 'failed'
            payroll_run.error_log += f"\nProcessing error: {str(e)}"
            payroll_run.save()
            logger.error(f"Payroll run processing failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ===========================
# SALARY SLIP VIEWSET
# ===========================

class SalarySlipViewSet(viewsets.ModelViewSet):
    """
    API endpoint for salary slip management
    Core functionality for salary slip CRUD and workflows
    """
    queryset = SalarySlip.objects.select_related(
        'employee_salary_info__user', 'payroll_run'
    ).all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return SalarySlipListSerializer
        return SalarySlipSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        # Filter by month/year
        month = self.request.query_params.get('month')
        year = self.request.query_params.get('year')
        if month:
            queryset = queryset.filter(month=month)
        if year:
            queryset = queryset.filter(year=year)
        
        # Filter by employee (User UUID) — used by the ESS self-service portal
        employee_uid = self.request.query_params.get('employee')
        if employee_uid:
            queryset = queryset.filter(employee_salary_info__user_id=employee_uid)

        # Filter by employee (biometric code)
        employee_id = self.request.query_params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(employee_salary_info__employee_id=employee_id)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(slip_number__icontains=search) |
                Q(employee_salary_info__employee_id__icontains=search) |
                Q(employee_salary_info__user__first_name__icontains=search) |
                Q(employee_salary_info__user__last_name__icontains=search)
            )
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get salary slip statistics for dashboard"""
        # Overall stats
        total_slips = SalarySlip.objects.count()
        by_status = SalarySlip.objects.values('status').annotate(count=Count('id'))
        
        status_counts = {item['status']: item['count'] for item in by_status}
        
        # Current month stats
        now = timezone.now()
        current_month_slips = SalarySlip.objects.filter(
            month=now.month,
            year=now.year
        ).count()
        
        # Total employees
        total_employees = EmployeeSalaryInfo.objects.filter(is_active=True).count()
        
        # Total payroll (current year)
        total_payroll = SalarySlip.objects.filter(
            year=now.year,
            status__in=[SalaryStatus.APPROVED, SalaryStatus.SENT]
        ).aggregate(total=Sum('net_salary'))['total'] or Decimal('0.00')
        
        data = {
            'total_slips': total_slips,
            'generated': status_counts.get(SalaryStatus.GENERATED, 0),
            'pending_approval': status_counts.get(SalaryStatus.PENDING_APPROVAL, 0),
            'approved': status_counts.get(SalaryStatus.APPROVED, 0),
            'sent': status_counts.get(SalaryStatus.SENT, 0),
            'total_employees': total_employees,
            'total_payroll': str(total_payroll),
            'current_month_slips': current_month_slips,
        }
        
        serializer = SalarySlipStatsSerializer(data)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def generate_pdf(self, request, pk=None):
        """Generate PDF for a salary slip"""
        salary_slip = self.get_object()
        
        try:
            from .salary_pdf_service import SalarySlipPDFService
            pdf_service = SalarySlipPDFService()
            pdf_path = pdf_service.generate_pdf(salary_slip)
            
            salary_slip.pdf_file_path = pdf_path
            salary_slip.pdf_generated_at = timezone.now()
            salary_slip.save()
            
            # Log action
            SalarySlipAuditLog.objects.create(
                salary_slip=salary_slip,
                action='generated',
                performed_by=request.user,
                description='PDF generated'
            )
            
            return Response({
                'message': 'PDF generated successfully',
                'pdf_path': pdf_path
            })
        except Exception as e:
            logger.error(f"PDF generation failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def send_email(self, request, pk=None):
        """Send salary slip via email to employee"""
        salary_slip = self.get_object()
        
        if salary_slip.status not in [SalaryStatus.APPROVED, SalaryStatus.SENT]:
            return Response(
                {'error': 'Only approved slips can be sent'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from .salary_email_service import SalarySlipEmailService
            email_service = SalarySlipEmailService()
            email_service.send_salary_slip_email(salary_slip, request.user)
            
            salary_slip.status = SalaryStatus.SENT
            salary_slip.save()
            
            return Response({'message': 'Email sent successfully'})
        except Exception as e:
            logger.error(f"Email sending failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def bulk_send_email(self, request):
        """Send salary slips to multiple employees"""
        serializer = SendSalarySlipEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        slip_ids = serializer.validated_data['salary_slip_ids']
        custom_message = serializer.validated_data.get('custom_message', '')
        
        success_count = 0
        failed_count = 0
        
        from .salary_email_service import SalarySlipEmailService
        email_service = SalarySlipEmailService()
        
        for slip_id in slip_ids:
            try:
                salary_slip = SalarySlip.objects.get(id=slip_id)
                email_service.send_salary_slip_email(
                    salary_slip, 
                    request.user,
                    custom_message=custom_message
                )
                salary_slip.status = SalaryStatus.SENT
                salary_slip.save()
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send email for slip {slip_id}: {str(e)}")
                failed_count += 1
        
        return Response({
            'message': f'Emails processed: {success_count} sent, {failed_count} failed',
            'success_count': success_count,
            'failed_count': failed_count
        })
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a salary slip"""
        salary_slip = self.get_object()
        
        if salary_slip.status != SalaryStatus.PENDING_APPROVAL:
            return Response(
                {'error': 'Only pending slips can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        salary_slip.status = SalaryStatus.APPROVED
        salary_slip.approved_by = request.user
        salary_slip.approved_at = timezone.now()
        salary_slip.save()
        
        # Log action
        SalarySlipAuditLog.objects.create(
            salary_slip=salary_slip,
            action='approved',
            performed_by=request.user,
            description='Salary slip approved'
        )
        
        return Response({'message': 'Salary slip approved successfully'})
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a salary slip"""
        salary_slip = self.get_object()
        reason = request.data.get('reason', '')
        
        if salary_slip.status != SalaryStatus.PENDING_APPROVAL:
            return Response(
                {'error': 'Only pending slips can be rejected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        salary_slip.status = SalaryStatus.REJECTED
        salary_slip.rejection_reason = reason
        salary_slip.save()
        
        # Log action
        SalarySlipAuditLog.objects.create(
            salary_slip=salary_slip,
            action='rejected',
            performed_by=request.user,
            description=f'Salary slip rejected: {reason}'
        )
        
        return Response({'message': 'Salary slip rejected'})


# ===========================
# APPROVAL WORKFLOW VIEWSET
# ===========================

class SalarySlipApprovalViewSet(viewsets.ModelViewSet):
    """
    API endpoint for salary slip approval workflow
    """
    queryset = SalarySlipApproval.objects.select_related(
        'salary_slip', 'approver'
    ).all()
    serializer_class = SalarySlipApprovalSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=True, methods=['post'])
    def decide(self, request, pk=None):
        """Make approval decision"""
        approval = self.get_object()
        serializer = ApprovalDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        decision = serializer.validated_data['decision']
        comments = serializer.validated_data.get('comments', '')
        
        if decision == 'approve':
            approval.status = ApprovalStatus.APPROVED
        else:
            approval.status = ApprovalStatus.REJECTED
        
        approval.decision_date = timezone.now()
        approval.comments = comments
        approval.save()
        
        return Response({'message': f'Approval {decision}d successfully'})


# ===========================
# EMAIL TRACKING VIEWSET
# ===========================

class SalarySlipEmailViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for salary slip email tracking
    Read-only access to email delivery logs
    """
    queryset = SalarySlipEmail.objects.select_related('salary_slip').all()
    serializer_class = SalarySlipEmailSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        return queryset


# ===========================
# AUDIT LOG VIEWSET
# ===========================

class SalarySlipAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for salary slip audit logs
    Read-only access to audit trail
    """
    queryset = SalarySlipAuditLog.objects.select_related(
        'salary_slip', 'performed_by'
    ).all()
    serializer_class = SalarySlipAuditLogSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by slip
        slip_id = self.request.query_params.get('slip_id')
        if slip_id:
            queryset = queryset.filter(salary_slip__id=slip_id)
        
        # Filter by action
        action = self.request.query_params.get('action')
        if action:
            queryset = queryset.filter(action=action)
        
        return queryset
