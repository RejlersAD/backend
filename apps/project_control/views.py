"""Project Management — DRF ViewSets.

URL roots (wired in apps.project_control.urls):
    /api/v1/project-control/estimates/
    /api/v1/project-control/estimate-line-items/
    /api/v1/project-control/wbs-nodes/
    /api/v1/project-control/documents/
    /api/v1/project-control/cost-snapshots/
    /api/v1/project-control/change-events/
    /api/v1/project-control/planning-packages/
    /api/v1/project-control/analytics/...
    /api/v1/project-control/phase-flags/
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.core.project_models import Project

from .access import (
    ProjectControlObjectPermission, accessible_enterprise_projects,
    can_approve_commercial, can_write_enterprise_project,
)
from .config import (
    DOCUMENT_KINDS, MAX_DOCUMENT_BYTES, PHASE_FLAGS,
    PLANNING_PACKAGE_ALLOWED_HTTP_METHODS, VARIANCE_THRESHOLDS,
    is_phase_enabled,
)
from .models import (
    ApprovedHourEntry, BudgetAllocation, ChangeEvent, ControlAccount, CostAllocation, CostLedgerEntry,
    CostSnapshot, Estimate, EstimateLineItem, IntegratedReportingSnapshot,
    PlanningPackage, ProjectDocument, ReconciliationRun, ReportingPeriod, ReportingPeriodAudit, WBSNode,
)
from .serializers import (
    ApprovedHourEntrySerializer, BudgetAllocationSerializer, ChangeEventSerializer, ControlAccountSerializer, CostAllocationSerializer,
    CostLedgerEntrySerializer, CostSnapshotSerializer,
    IntegratedReportingSnapshotSerializer, ReconciliationRunSerializer,
    EstimateLineItemSerializer, EstimateListSerializer, EstimateSerializer,
    PlanningPackageListSerializer, PlanningPackageSerializer,
    ProjectDocumentSerializer, ReportingPeriodAuditSerializer, ReportingPeriodSerializer,
    WBSNodeSerializer,
)
from .services.excel_import import import_boq_excel
from .services.finance_sync import sync_project_spend
from .services.kpis import compute_project_kpis
from .services.portfolio_exceptions import build_portfolio_exception_dashboard
from .services.cost_ledger import source_record
from .services.actuals import create_integrated_snapshot, reconcile_reporting_period
from .services.commercial_dashboard import project_commercial_dashboard
from .services.s3 import presign_document_download
from .services.variance import compute_variance
from .tasks import parse_uploaded_document

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Read-only phase flags endpoint — keeps frontend in sync without a rebuild.
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def phase_flags_view(request):
    return Response({
        'phase_flags': PHASE_FLAGS,
        'variance_thresholds': VARIANCE_THRESHOLDS,
        'document_kinds': DOCUMENT_KINDS,
        'max_document_bytes': MAX_DOCUMENT_BYTES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Filter mixin — every viewset accepts ?project=<id>
# ─────────────────────────────────────────────────────────────────────────────
class _ProjectFilteredMixin:
    def get_queryset(self):
        qs = super().get_queryset().filter(
            is_deleted=False,
            project__in=accessible_enterprise_projects(self.request.user),
        )
        pid = self.request.query_params.get('project')
        if pid:
            qs = qs.filter(project_id=pid)
        return qs

    def perform_create(self, serializer):
        project = serializer.validated_data.get('project')
        if project and not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')
        serializer.save()


# ─────────────────────────────────────────────────────────────────────────────
# Estimate viewset
# ─────────────────────────────────────────────────────────────────────────────
class EstimateViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    queryset = Estimate.objects.all().select_related('project').prefetch_related('line_items')

    def get_serializer_class(self):
        if self.action == 'list':
            return EstimateListSerializer
        return EstimateSerializer

    def perform_create(self, serializer):
        project = serializer.validated_data.get('project')
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        est = self.get_object()
        est.status = 'approved'
        est.save(update_fields=['status', 'updated_at'])
        return Response(EstimateSerializer(est).data)

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        est = self.get_object()
        est.status = 'superseded'
        est.save(update_fields=['status', 'updated_at'])
        return Response(EstimateSerializer(est).data)


class EstimateLineItemViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = EstimateLineItemSerializer
    queryset = EstimateLineItem.objects.all().filter(is_deleted=False)

    def get_queryset(self):
        qs = super().get_queryset().filter(estimate__project__in=accessible_enterprise_projects(self.request.user))
        est = self.request.query_params.get('estimate')
        if est:
            qs = qs.filter(estimate_id=est)
        return qs

    def perform_create(self, serializer):
        project = serializer.validated_data['estimate'].project
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')
        serializer.save()


# ─────────────────────────────────────────────────────────────────────────────
# WBS viewset
# ─────────────────────────────────────────────────────────────────────────────
class WBSNodeViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = WBSNodeSerializer
    queryset = WBSNode.objects.all()


class ControlAccountViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    """Governed WBS responsibility with submit/approve/close transitions."""

    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = ControlAccountSerializer
    queryset = ControlAccount.objects.select_related(
        'project', 'wbs_node', 'manager', 'created_by', 'submitted_by', 'approved_by', 'closed_by',
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        requested_status = self.request.query_params.get('status')
        return queryset.filter(status=requested_status) if requested_status else queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')
        serializer.save(created_by=self.request.user, status='draft')

    def perform_update(self, serializer):
        if serializer.instance.status != 'draft':
            raise ValidationError('Only draft Control Accounts can be edited.')
        super().perform_update(serializer)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        with transaction.atomic():
            account = ControlAccount.objects.select_for_update().get(pk=self.get_object().pk)
            if account.status != 'draft':
                raise ValidationError('Only a draft Control Account can be submitted.')
            account.status = 'submitted'
            account.submitted_by = request.user
            account.submitted_at = timezone.now()
            account.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        return Response(self.get_serializer(account).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Project Control or Finance approval access is required.')
        with transaction.atomic():
            account = ControlAccount.objects.select_for_update().get(pk=self.get_object().pk)
            if account.status != 'submitted':
                raise ValidationError('Only a submitted Control Account can be approved.')
            if account.submitted_by_id == request.user.id and not request.user.is_superuser:
                raise PermissionDenied('The submitter cannot approve their own Control Account.')
            approved_budget = BudgetAllocation.objects.filter(
                project=account.project, wbs_node=account.wbs_node,
                status='approved', is_deleted=False,
            ).aggregate(total=Sum('amount'))['total'] or 0
            if approved_budget <= 0:
                raise ValidationError({
                    'approved_budget': 'Approve at least one budget allocation for this WBS before activation.',
                })
            account.status = 'active'
            account.approved_by = request.user
            account.approved_at = timezone.now()
            account.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        return Response(self.get_serializer(account).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Project Control or Finance approval access is required.')
        with transaction.atomic():
            account = ControlAccount.objects.select_for_update().get(pk=self.get_object().pk)
            if account.status != 'active':
                raise ValidationError('Only an active Control Account can be closed.')
            account.status = 'closed'
            account.closed_by = request.user
            account.closed_at = timezone.now()
            account.save(update_fields=['status', 'closed_by', 'closed_at', 'updated_at'])
        return Response(self.get_serializer(account).data)


class ReportingPeriodViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    """Controlled project reporting calendar and immutable transition history."""

    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = ReportingPeriodSerializer
    queryset = ReportingPeriod.objects.select_related(
        'project', 'created_by', 'submitted_by', 'locked_by', 'reopened_by',
    ).prefetch_related('audit_events')

    def get_queryset(self):
        queryset = super().get_queryset()
        requested_status = self.request.query_params.get('status')
        return queryset.filter(status=requested_status) if requested_status else queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')
        if ReportingPeriod.objects.filter(
            project=project, status__in=['open', 'reopened'], is_deleted=False,
        ).exists():
            raise ValidationError({
                'period': 'Submit the current open reporting period before opening another one.',
            })
        with transaction.atomic():
            period = serializer.save(created_by=self.request.user, status='open')
            ReportingPeriodAudit.objects.create(
                period=period, actor=self.request.user, action='created', to_status='open',
            )

    def perform_update(self, serializer):
        if serializer.instance.status not in {'open', 'reopened'}:
            raise ValidationError('Submitted and locked reporting periods are immutable.')
        before = serializer.instance.status
        with transaction.atomic():
            period = serializer.save()
            ReportingPeriodAudit.objects.create(
                period=period, actor=self.request.user, action='updated',
                from_status=before, to_status=period.status,
            )

    def _transition(self, request, period, *, expected, target, action_name, reason=''):
        if period.status not in expected:
            expected_text = ' or '.join(sorted(expected))
            raise ValidationError(f'Only a {expected_text} reporting period can be {action_name}.')
        previous = period.status
        now = timezone.now()
        period.status = target
        update_fields = ['status', 'updated_at']
        if target == 'submitted':
            period.submitted_by = request.user
            period.submitted_at = now
            update_fields += ['submitted_by', 'submitted_at']
        elif target == 'locked':
            period.locked_by = request.user
            period.locked_at = now
            update_fields += ['locked_by', 'locked_at']
        elif target == 'reopened':
            period.reopened_by = request.user
            period.reopened_at = now
            period.reopen_reason = reason
            update_fields += ['reopened_by', 'reopened_at', 'reopen_reason']
        period.save(update_fields=update_fields)
        ReportingPeriodAudit.objects.create(
            period=period, actor=request.user, action=action_name,
            from_status=previous, to_status=target, reason=reason,
        )
        return period

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        period = self.get_object()
        reconciliation = reconcile_reporting_period(period, user=request.user)
        if reconciliation.exception_count:
            raise ValidationError({
                'reconciliation': 'Resolve reconciliation exceptions before submitting the period.',
                'run': ReconciliationRunSerializer(reconciliation).data,
            })
        with transaction.atomic():
            period = ReportingPeriod.objects.select_for_update().get(pk=period.pk)
            self._transition(
                request, period, expected={'open', 'reopened'}, target='submitted', action_name='submitted',
            )
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Project Control or Finance approval access is required to lock a period.')
        with transaction.atomic():
            period = ReportingPeriod.objects.select_for_update().get(pk=self.get_object().pk)
            if period.submitted_by_id == request.user.id and not request.user.is_superuser:
                raise PermissionDenied('The submitter cannot lock their own reporting period.')
            try:
                snapshot = create_integrated_snapshot(period, user=request.user)
            except ValueError as exc:
                raise ValidationError({'snapshot': str(exc)}) from exc
            self._transition(request, period, expected={'submitted'}, target='locked', action_name='locked')
        result = self.get_serializer(period).data
        result['snapshot'] = IntegratedReportingSnapshotSerializer(snapshot).data
        return Response(result)

    @action(detail=True, methods=['post'])
    def reconcile(self, request, pk=None):
        period = self.get_object()
        if not can_write_enterprise_project(request.user, period.project):
            raise PermissionDenied('You cannot reconcile actuals for this project.')
        try:
            run = reconcile_reporting_period(period, user=request.user)
        except ValueError as exc:
            raise ValidationError({'reconciliation': str(exc)}) from exc
        return Response(ReconciliationRunSerializer(run).data)

    @action(detail=True, methods=['get'], url_path='reconciliations')
    def reconciliations(self, request, pk=None):
        period = self.get_object()
        return Response(ReconciliationRunSerializer(period.reconciliation_runs.all(), many=True).data)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Project Control or Finance approval access is required to reopen a period.')
        reason = str(request.data.get('reason') or '').strip()
        if len(reason) < 10:
            raise ValidationError({'reason': 'Provide a reopen reason of at least 10 characters.'})
        with transaction.atomic():
            period = ReportingPeriod.objects.select_for_update().get(pk=self.get_object().pk)
            if ReportingPeriod.objects.filter(
                project=period.project, status__in=['open', 'reopened'], is_deleted=False,
            ).exclude(pk=period.pk).exists():
                raise ValidationError({
                    'period': 'Submit the current open reporting period before reopening this one.',
                })
            self._transition(
                request, period, expected={'locked'}, target='reopened',
                action_name='reopened', reason=reason,
            )
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        period = self.get_object()
        return Response(ReportingPeriodAuditSerializer(period.audit_events.all(), many=True).data)


class ApprovedHourEntryViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    """Two-person approval workflow for project-attributed labour actuals."""

    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = ApprovedHourEntrySerializer
    queryset = ApprovedHourEntry.objects.select_related(
        'project', 'control_account', 'reporting_period', 'created_by',
        'submitted_by', 'approved_by', 'reversed_by',
    )

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot enter hours for this project.')
        serializer.save(
            created_by=self.request.user, status='draft', currency=project.currency or 'AED',
        )

    def perform_update(self, serializer):
        if serializer.instance.status != 'draft':
            raise ValidationError('Only draft hour entries can be edited.')
        super().perform_update(serializer)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        with transaction.atomic():
            entry = ApprovedHourEntry.objects.select_for_update().get(pk=self.get_object().pk)
            if entry.status != 'draft':
                raise ValidationError('Only a draft hour entry can be submitted.')
            if not entry.reporting_period.is_entry_allowed:
                raise ValidationError('The reporting period is no longer open for entry.')
            entry.status = 'submitted'
            entry.submitted_by = request.user
            entry.submitted_at = timezone.now()
            entry.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Project Control or Finance approval access is required.')
        with transaction.atomic():
            entry = ApprovedHourEntry.objects.select_for_update().get(pk=self.get_object().pk)
            if entry.status != 'submitted':
                raise ValidationError('Only a submitted hour entry can be approved.')
            if entry.submitted_by_id == request.user.id and not request.user.is_superuser:
                raise PermissionDenied('The submitter cannot approve their own hour entry.')
            if not entry.reporting_period.is_entry_allowed:
                raise ValidationError('The reporting period is no longer open for approval.')
            entry.status = 'approved'
            entry.labor_actual_cost = entry.hours * entry.hourly_cost_rate
            entry.approved_by = request.user
            entry.approved_at = timezone.now()
            entry.save(update_fields=[
                'status', 'labor_actual_cost', 'approved_by', 'approved_at', 'updated_at',
            ])
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=['post'])
    def reverse(self, request, pk=None):
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Project Control or Finance approval access is required.')
        reason = str(request.data.get('reason') or '').strip()
        if len(reason) < 10:
            raise ValidationError({'reason': 'Provide a reversal reason of at least 10 characters.'})
        with transaction.atomic():
            entry = ApprovedHourEntry.objects.select_for_update().get(pk=self.get_object().pk)
            if entry.status != 'approved':
                raise ValidationError('Only an approved hour entry can be reversed.')
            if not entry.reporting_period.is_entry_allowed:
                raise ValidationError('Reopen the reporting period before reversing approved hours.')
            entry.status = 'reversed'
            entry.reversed_by = request.user
            entry.reversed_at = timezone.now()
            entry.reversal_reason = reason
            entry.save(update_fields=[
                'status', 'reversed_by', 'reversed_at', 'reversal_reason', 'updated_at',
            ])
            CostLedgerEntry.objects.filter(entry_key=f'approved-hour:{entry.pk}').update(status='reversed')
        return Response(self.get_serializer(entry).data)


class BudgetAllocationViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = BudgetAllocationSerializer
    queryset = BudgetAllocation.objects.select_related(
        'project', 'wbs_node', 'source_budget', 'approved_by',
    )

    def perform_update(self, serializer):
        if serializer.instance.status == 'approved':
            raise ValidationError('Approved budgets are immutable; create a correcting allocation.')
        super().perform_update(serializer)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        allocation = self.get_object()
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Finance or Project Control approval access is required.')
        allocation.status = 'approved'
        allocation.approved_by = request.user
        allocation.approved_at = timezone.now()
        allocation.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        sync_project_spend(allocation.project, user=request.user)
        return Response(self.get_serializer(allocation).data)


class CostAllocationViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = CostAllocationSerializer
    queryset = CostAllocation.objects.select_related(
        'project', 'wbs_node', 'budget_allocation', 'allocated_by', 'approved_by',
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        for field in ('source_type', 'source_id', 'status'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')
        serializer.save(allocated_by=self.request.user, status='draft')

    def perform_update(self, serializer):
        if serializer.instance.status == 'approved':
            raise ValidationError('Approved cost allocations are immutable; reject and replace them.')
        super().perform_update(serializer)

    def _sync_affected_projects(self, allocation, user):
        projects = {str(allocation.project_id): allocation.project}
        source = source_record(allocation.source_type, allocation.source_id)
        source_project = None
        if allocation.source_type == 'purchase_order' and source:
            source_project = source.enterprise_project
        elif allocation.source_type == 'invoice_allocation' and source:
            source_project = source.purchase_order.enterprise_project
        if source_project:
            projects[str(source_project.pk)] = source_project
        for related in CostAllocation.objects.filter(
            source_type=allocation.source_type, source_id=allocation.source_id,
            is_deleted=False,
        ).select_related('project'):
            projects[str(related.project_id)] = related.project
        for project in projects.values():
            sync_project_spend(project, user=user)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        allocation = self.get_object()
        if allocation.allocated_by_id == request.user.id and not request.user.is_superuser:
            raise PermissionDenied('The allocator cannot approve their own cost allocation.')
        if not can_approve_commercial(request.user):
            raise PermissionDenied('Finance or Project Control approval access is required.')
        allocation.status = 'approved'
        allocation.approved_by = request.user
        allocation.approved_at = timezone.now()
        allocation.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        self._sync_affected_projects(allocation, request.user)
        return Response(self.get_serializer(allocation).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        allocation = self.get_object()
        allocation.status = 'rejected'
        allocation.approved_by = request.user
        allocation.approved_at = timezone.now()
        allocation.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        self._sync_affected_projects(allocation, request.user)
        return Response(self.get_serializer(allocation).data)


class CostLedgerEntryViewSet(_ProjectFilteredMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = CostLedgerEntrySerializer
    queryset = CostLedgerEntry.objects.select_related(
        'project', 'wbs_node', 'budget_allocation', 'cost_allocation',
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        for field in ('entry_type', 'status', 'source_type'):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset


# ─────────────────────────────────────────────────────────────────────────────
# Document viewset — file uploads + presign + Excel-import shortcut
# ─────────────────────────────────────────────────────────────────────────────
class ProjectDocumentViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = ProjectDocumentSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = ProjectDocument.objects.all().select_related('project', 'uploaded_by')

    def perform_create(self, serializer):
        file = serializer.validated_data.get('file')
        project = serializer.validated_data.get('project')
        if not can_write_enterprise_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project.')

        doc = serializer.save(
            uploaded_by=self.request.user if self.request.user.is_authenticated else None,
            original_filename=getattr(file, 'name', ''),
            content_type=getattr(file, 'content_type', ''),
            size_bytes=getattr(file, 'size', 0) or 0,
            parse_status='queued',
        )
        # Best-effort async; fall back to inline on broker failure.
        try:
            parse_uploaded_document.delay(doc.id)
        except Exception as exc:  # noqa: BLE001
            logger.info('parse_uploaded_document.delay failed (%s); running inline', exc)
            try:
                parse_uploaded_document(doc.id)
            except Exception as inner:  # noqa: BLE001
                logger.warning('inline parse_uploaded_document failed: %s', inner)

    @action(detail=True, methods=['get'], url_path='presign-download')
    def presign_download(self, request, pk=None):
        doc = self.get_object()
        url = presign_document_download(doc)
        return Response({'document_id': doc.id, 'download_url': url})

    @action(detail=False, methods=['post'], url_path='import-boq')
    def import_boq(self, request):
        """Upload an Excel BOQ + create an Estimate in one shot."""
        project_id = request.data.get('project')
        if not project_id:
            return Response({'error': 'project is required'}, status=status.HTTP_400_BAD_REQUEST)
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'file is required'}, status=status.HTTP_400_BAD_REQUEST)
        if file.size > MAX_DOCUMENT_BYTES:
            return Response(
                {'error': f'File exceeds the {MAX_DOCUMENT_BYTES} byte limit.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            project = accessible_enterprise_projects(request.user).get(pk=project_id)
        except Project.DoesNotExist:
            return Response({'error': 'project not found'}, status=status.HTTP_404_NOT_FOUND)

        kind = request.data.get('kind') or 'estimate'
        title = request.data.get('title', '')
        notes = request.data.get('notes', '')

        # Save as document first so the estimate retains an audit link.
        doc = ProjectDocument.objects.create(
            project=project,
            kind='boq',
            title=title or file.name,
            file=file,
            original_filename=file.name,
            content_type=getattr(file, 'content_type', ''),
            size_bytes=getattr(file, 'size', 0) or 0,
            uploaded_by=request.user if request.user.is_authenticated else None,
            parse_status='done',
        )
        try:
            doc.file.open('rb')
            summary = import_boq_excel(
                project=project, file_obj=doc.file, kind=kind,
                title=title, notes=notes, user=request.user, source_document=doc,
            )
        except Exception as exc:  # noqa: BLE001
            doc.parse_status = 'failed'
            doc.parse_error = str(exc)
            doc.save(update_fields=['parse_status', 'parse_error', 'updated_at'])
            return Response({'error': str(exc), 'document_id': doc.id},
                            status=status.HTTP_400_BAD_REQUEST)
        finally:
            try:
                doc.file.close()
            except Exception:
                pass

        return Response({'document': ProjectDocumentSerializer(doc).data, 'summary': summary},
                        status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot + Change viewsets (Phase 3/4 read mostly)
# ─────────────────────────────────────────────────────────────────────────────
class CostSnapshotViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = CostSnapshotSerializer
    queryset = CostSnapshot.objects.all()


class IntegratedReportingSnapshotViewSet(_ProjectFilteredMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = IntegratedReportingSnapshotSerializer
    queryset = IntegratedReportingSnapshot.objects.select_related(
        'project', 'reporting_period', 'reconciliation_run', 'sealed_by',
    )

    def get_queryset(self):
        queryset = self.queryset.filter(project__in=accessible_enterprise_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset


class ChangeEventViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    serializer_class = ChangeEventSerializer
    queryset = ChangeEvent.objects.all()


class PlanningPackageViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    """
    SOFT-CODED: Planning Package ViewSet
    
    Endpoints:
        GET    /api/v1/project-control/planning-packages/        - List packages
        POST   /api/v1/project-control/planning-packages/        - Create package
        GET    /api/v1/project-control/planning-packages/{id}/   - Get detail
        PUT    /api/v1/project-control/planning-packages/{id}/   - Update
        PATCH  /api/v1/project-control/planning-packages/{id}/   - Partial update

    NOTE: Deletion is intentionally disabled (SOFT-CODED via
    PLANNING_PACKAGE_ALLOWED_HTTP_METHODS in config.py) — DELETE requests
    return 405 Method Not Allowed. Packages are never removable from this API.

    Query params:
        ?project={id}   - Filter by project
        ?status={value} - Filter by status
        ?priority={value} - Filter by priority
    """
    permission_classes = [IsAuthenticated, ProjectControlObjectPermission]
    http_method_names = PLANNING_PACKAGE_ALLOWED_HTTP_METHODS
    queryset = PlanningPackage.objects.all().select_related(
        'project', 'package_manager', 'wbs_node'
    )
    
    def get_serializer_class(self):
        """Use lightweight serializer for list view"""
        if self.action == 'list':
            return PlanningPackageListSerializer
        return PlanningPackageSerializer
    
    def get_queryset(self):
        """Override to add status/priority filtering"""
        qs = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        
        # Filter by priority
        priority_filter = self.request.query_params.get('priority')
        if priority_filter:
            qs = qs.filter(priority=priority_filter)
        
        return qs
    
    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """
        Get statistics for planning packages
        Query params: ?project={id}
        """
        project_id = request.query_params.get('project')
        if not project_id:
            return Response(
                {'error': 'project parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        packages = self.get_queryset().filter(project_id=project_id)
        
        from django.db.models import Sum, Count, Avg
        
        stats = {
            'total_packages': packages.count(),
            'by_status': {
                'draft': packages.filter(status='draft').count(),
                'active': packages.filter(status='active').count(),
                'completed': packages.filter(status='completed').count(),
                'on_hold': packages.filter(status='on_hold').count(),
                'cancelled': packages.filter(status='cancelled').count(),
            },
            'by_priority': {
                'low': packages.filter(priority='low').count(),
                'medium': packages.filter(priority='medium').count(),
                'high': packages.filter(priority='high').count(),
                'critical': packages.filter(priority='critical').count(),
            },
            'financial': {
                'total_budget': packages.aggregate(Sum('budget'))['budget__sum'] or 0,
                'total_actual_cost': packages.aggregate(Sum('actual_cost'))['actual_cost__sum'] or 0,
            },
            'progress': {
                'average_progress': packages.aggregate(Avg('progress_percentage'))['progress_percentage__avg'] or 0,
                'completed_count': packages.filter(progress_percentage=100).count(),
            }
        }
        
        return Response(stats)


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — Phase 1 endpoints + Phase 2-4 stubs (501)
# ─────────────────────────────────────────────────────────────────────────────
class ProjectAnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    basename = 'project-control-analytics'

    def _get_project(self, request):
        pid = request.query_params.get('project') or request.data.get('project')
        if not pid:
            return None, Response({'error': 'project is required'},
                                  status=status.HTTP_400_BAD_REQUEST)
        try:
            project = accessible_enterprise_projects(request.user).get(pk=pid)
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and not can_write_enterprise_project(request.user, project):
                return None, Response({'error': 'You cannot modify this project.'}, status=status.HTTP_403_FORBIDDEN)
            return project, None
        except Project.DoesNotExist:
            return None, Response({'error': 'project not found'},
                                  status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'], url_path='cost-kpis')
    def cost_kpis(self, request):
        if not is_phase_enabled('phase_1_cost_dashboard'):
            return Response({'error': 'phase_1_cost_dashboard disabled'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        project, err = self._get_project(request)
        if err:
            return err
        return Response(compute_project_kpis(project))

    @action(detail=False, methods=['get'], url_path='commercial-dashboard')
    def commercial_dashboard(self, request):
        project, err = self._get_project(request)
        if err:
            return err
        return Response(project_commercial_dashboard(project))

    @action(detail=False, methods=['get'], url_path='portfolio-exceptions')
    def portfolio_exceptions(self, request):
        """Management-by-exception view across every accessible enterprise project."""
        return Response(build_portfolio_exception_dashboard(
            accessible_enterprise_projects(request.user),
            severity=request.query_params.get('severity'),
            category=request.query_params.get('category'),
            search=request.query_params.get('search'),
        ))

    @action(detail=False, methods=['get'], url_path='estimate-variance')
    def estimate_variance(self, request):
        if not is_phase_enabled('phase_1_estimate_variance'):
            return Response({'error': 'phase_1_estimate_variance disabled'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        project, err = self._get_project(request)
        if err:
            return err
        base_id = request.query_params.get('base')
        cmp_id  = request.query_params.get('compare')
        group_by = request.query_params.get('group_by', 'wbs')
        base = Estimate.objects.filter(pk=base_id, is_deleted=False).first() if base_id else None
        cmp_ = Estimate.objects.filter(pk=cmp_id,  is_deleted=False).first() if cmp_id  else None
        return Response(compute_variance(
            project=project, base_estimate=base, compare_estimate=cmp_, group_by=group_by,
        ))

    @action(detail=False, methods=['post'], url_path='finance-sync')
    def finance_sync(self, request):
        if not is_phase_enabled('phase_1_finance_sync'):
            return Response({'error': 'phase_1_finance_sync disabled'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        project, err = self._get_project(request)
        if err:
            return err
        return Response(sync_project_spend(project, user=request.user))

    # Phase 2/3/4 stubs — all return 501 with a uniform shape so the frontend
    # can render a "Coming in Phase X" card without bespoke handling.
    def _phase_stub(self, request, flag, label):
        return Response({
            'error': 'not_implemented',
            'phase_flag': flag,
            'message': f'{label} ships when {flag.upper()} flag is enabled.',
        }, status=status.HTTP_501_NOT_IMPLEMENTED)

    @action(detail=False, methods=['post'], url_path='ai-takeoff')
    def ai_takeoff(self, request):
        if is_phase_enabled('phase_2_ai_takeoff'):
            return Response({'message': 'AI Take-Off endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_2_ai_takeoff', 'AI Take-Off')

    @action(detail=False, methods=['get'], url_path='evm')
    def evm(self, request):
        if is_phase_enabled('phase_3_evm_forecast'):
            return Response({'message': 'EVM endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_3_evm_forecast', 'EVM Forecasting')

    @action(detail=False, methods=['get'], url_path='cashflow')
    def cashflow(self, request):
        if is_phase_enabled('phase_3_cashflow_curve'):
            return Response({'message': 'Cashflow endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_3_cashflow_curve', 'Cashflow Curve')

    @action(detail=False, methods=['get'], url_path='risk')
    def risk(self, request):
        if is_phase_enabled('phase_4_risk_analytics'):
            return Response({'message': 'Risk endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_4_risk_analytics', 'Risk Analytics')

    @action(detail=False, methods=['post'], url_path='change-detection')
    def change_detection(self, request):
        if is_phase_enabled('phase_4_change_detection'):
            return Response({'message': 'Change Detection placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_4_change_detection', 'Change Detection')
