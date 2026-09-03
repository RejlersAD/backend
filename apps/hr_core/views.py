"""Canonical employee and reusable HR workflow APIs."""

from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .identity import EmployeeIdentityService
from .models import (
    EmployeeIdentityAlias,
    EmployeeMaster,
    HRWorkflowDefinition,
    HRWorkflowInstance,
    HRWorkflowStage,
)
from .serializers import (
    EmployeeIdentityAliasSerializer,
    EmployeeMasterDetailSerializer,
    EmployeeMasterListSerializer,
    HRWorkflowDefinitionSerializer,
    HRWorkflowInstanceSerializer,
    HRWorkflowStageSerializer,
)
from .workflows import HRWorkflowService


HR_ROLE_CODES = {'hr_manager', 'hr_admin', 'human_resource', 'admin', 'super_admin'}


def _role_codes(user):
    if user.is_superuser:
        return {'super_admin'}
    try:
        return set(user.rbac_profile.roles.filter(is_active=True).values_list('code', flat=True))
    except Exception:
        return set()


def _is_hr(user):
    return user.is_staff or user.is_superuser or bool(_role_codes(user) & HR_ROLE_CODES)


class EmployeeMasterViewSet(viewsets.ModelViewSet):
    """The authoritative employee directory and identity-health API."""

    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = [
        'first_name', 'last_name', 'email', 'employee_number',
        'employee_code', 'emp_code', 'department', 'designation',
    ]
    ordering_fields = ['first_name', 'last_name', 'join_date', 'department', 'updated_at']
    filterset_fields = ['employment_status', 'department', 'division', 'branch', 'manager']

    def get_queryset(self):
        queryset = EmployeeMaster.objects.select_related('user', 'manager__user').all()
        if _is_hr(self.request.user):
            return queryset
        return queryset.filter(Q(user=self.request.user) | Q(manager__user=self.request.user))

    def get_serializer_class(self):
        return EmployeeMasterListSerializer if self.action == 'list' else EmployeeMasterDetailSerializer

    def perform_update(self, serializer):
        employee = self.get_object()
        if not _is_hr(self.request.user) and employee.user_id != self.request.user.id:
            raise PermissionDenied('Only HR can update another employee record.')
        if not _is_hr(self.request.user):
            employee_editable = {
                'preferred_given_name', 'phone_number', 'country', 'city',
                'address', 'postal_code', 'photo',
            }
            forbidden = set(serializer.validated_data) - employee_editable
            if forbidden:
                raise PermissionDenied(
                    'Employees may only update their own contact details and profile photo.'
                )
        employee = serializer.save(last_updated_by=self.request.user)
        EmployeeIdentityService.register_aliases(employee)

    def create(self, request, *args, **kwargs):
        raise ValidationError({'detail': 'Use the onboarding employee-creation workflow.'})

    @action(detail=False, methods=['get'], url_path='resolve')
    def resolve(self, request):
        identifier = request.query_params.get('identifier')
        employee = EmployeeIdentityService.resolve(identifier, request.query_params.get('source'))
        if not employee or not self.get_queryset().filter(pk=employee.pk).exists():
            return Response({'detail': 'Employee was not found.'}, status=status.HTTP_404_NOT_FOUND)
        EmployeeIdentityService.register_aliases(employee)
        return Response(EmployeeMasterDetailSerializer(employee, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='identity-health')
    def identity_health(self, request, pk=None):
        employee = self.get_object()
        aliases = EmployeeIdentityService.register_aliases(employee)
        report = EmployeeIdentityService.consistency_report(employee)
        report['aliases'] = EmployeeIdentityAliasSerializer(
            employee.identity_aliases.all(), many=True
        ).data
        report['conflicts'] = [item for item in aliases if isinstance(item, dict)]
        return Response(report)

    @action(detail=True, methods=['post'], url_path='repair-identity')
    def repair_identity(self, request, pk=None):
        if not _is_hr(request.user):
            raise PermissionDenied('Only HR can repair cross-system employee identity data.')
        return Response(EmployeeIdentityService.repair_shared_fields(self.get_object()))


class EmployeeIdentityAliasViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EmployeeIdentityAliasSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['employee', 'source', 'identifier_type']
    search_fields = ['value', 'normalized_value']

    def get_queryset(self):
        queryset = EmployeeIdentityAlias.objects.select_related('employee').all()
        if _is_hr(self.request.user):
            return queryset
        return queryset.filter(employee__user=self.request.user)


class HRWorkflowDefinitionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = HRWorkflowDefinitionSerializer
    queryset = HRWorkflowDefinition.objects.prefetch_related('stages').all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['code', 'is_active', 'subject_type']
    search_fields = ['code', 'name', 'description']

    def _require_hr(self):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can configure workflow definitions.')

    def perform_create(self, serializer):
        self._require_hr()
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._require_hr()
        serializer.save()

    def perform_destroy(self, instance):
        self._require_hr()
        if instance.instances.exists():
            instance.is_active = False
            instance.save(update_fields=['is_active', 'updated_at'])
        else:
            instance.delete()


class HRWorkflowStageViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = HRWorkflowStageSerializer
    queryset = HRWorkflowStage.objects.select_related('definition').all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['definition', 'approver_type']

    def _require_hr(self):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can configure workflow stages.')

    def perform_create(self, serializer):
        self._require_hr()
        serializer.save()

    def perform_update(self, serializer):
        self._require_hr()
        serializer.save()

    def perform_destroy(self, instance):
        self._require_hr()
        if instance.tasks.exists():
            raise ValidationError({'detail': 'A stage used by workflow history cannot be deleted.'})
        instance.delete()


class HRWorkflowInstanceViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = HRWorkflowInstanceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'definition', 'employee', 'subject_type']
    search_fields = ['subject_id', 'employee__first_name', 'employee__last_name']

    def get_queryset(self):
        queryset = HRWorkflowInstance.objects.select_related(
            'definition', 'employee', 'current_stage', 'requested_by'
        ).prefetch_related('tasks__stage', 'tasks__assigned_to', 'events__actor')
        if _is_hr(self.request.user):
            return queryset
        roles = _role_codes(self.request.user)
        return queryset.filter(
            Q(requested_by=self.request.user)
            | Q(employee__user=self.request.user)
            | Q(employee__manager__user=self.request.user)
            | Q(tasks__assigned_to=self.request.user)
            | Q(tasks__assigned_role_code__in=roles)
        ).distinct()

    def create(self, request, *args, **kwargs):
        employee = EmployeeIdentityService.resolve(request.data.get('employee'))
        instance = HRWorkflowService.start(
            request.data.get('definition_code'),
            request.data.get('subject_type'),
            request.data.get('subject_id'),
            employee=employee,
            requested_by=request.user,
            context=request.data.get('context') or {},
        )
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    def _decision(self, request, decision):
        instance = self.get_object()
        HRWorkflowService.decide(instance, request.user, decision, request.data.get('note', ''))
        instance.refresh_from_db()
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._decision(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._decision(request, 'reject')

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        instance = self.get_object()
        if not (_is_hr(request.user) or instance.requested_by_id == request.user.id):
            raise PermissionDenied('Only the requester or HR can cancel this workflow.')
        HRWorkflowService.cancel(instance, request.user, request.data.get('note', ''))
        instance.refresh_from_db()
        return Response(self.get_serializer(instance).data)
