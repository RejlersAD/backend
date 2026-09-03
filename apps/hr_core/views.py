"""Canonical employee and reusable HR workflow APIs."""

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .identity import EmployeeIdentityService
from .models import (
    ContinuousFeedback,
    DevelopmentAction,
    DevelopmentPlan,
    EmployeeServiceRequest,
    EmployeeIdentityAlias,
    EmployeeMaster,
    GoalCheckIn,
    HRWorkflowDefinition,
    HRWorkflowInstance,
    HRWorkflowStage,
    OvertimeRequest,
    PerformanceCycle,
    PerformanceGoal,
    PerformanceReview,
    PromotionCase,
    ShiftAssignment,
    ShiftRoster,
    SuccessionCandidate,
    SuccessionPlan,
    TalentAssessment,
    WorkShift,
)
from .serializers import (
    ContinuousFeedbackSerializer,
    DevelopmentActionSerializer,
    DevelopmentPlanSerializer,
    EmployeeServiceRequestSerializer,
    EmployeeIdentityAliasSerializer,
    EmployeeMasterDetailSerializer,
    EmployeeMasterListSerializer,
    HRWorkflowDefinitionSerializer,
    HRWorkflowInstanceSerializer,
    HRWorkflowStageSerializer,
    GoalCheckInSerializer,
    OvertimeRequestSerializer,
    PerformanceCycleSerializer,
    PerformanceGoalSerializer,
    PerformanceReviewSerializer,
    PromotionCaseSerializer,
    ShiftAssignmentSerializer,
    ShiftRosterSerializer,
    SuccessionCandidateSerializer,
    SuccessionPlanSerializer,
    TalentAssessmentSerializer,
    WorkShiftSerializer,
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


def _employee_scope(user):
    if _is_hr(user):
        return Q()
    return Q(user=user) | Q(manager__user=user)


class PerformanceCycleViewSet(viewsets.ModelViewSet):
    serializer_class = PerformanceCycleSerializer
    permission_classes = [IsAuthenticated]
    queryset = PerformanceCycle.objects.all()
    filterset_fields = ['status', 'start_date', 'end_date']
    search_fields = ['name']

    def perform_create(self, serializer):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can create performance cycles.')
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can update performance cycles.')
        serializer.save()

    def perform_destroy(self, instance):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can delete performance cycles.')
        if instance.status != 'draft':
            raise ValidationError({'status': 'Only draft cycles can be deleted.'})
        instance.delete()


class PerformanceGoalViewSet(viewsets.ModelViewSet):
    serializer_class = PerformanceGoalSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['employee', 'cycle', 'goal_type', 'status']
    search_fields = ['title', 'description', 'metric_name']
    ordering_fields = ['due_date', 'weight', 'progress', 'created_at']

    def get_queryset(self):
        return PerformanceGoal.objects.select_related('employee', 'cycle').prefetch_related('check_ins').filter(
            employee__in=EmployeeMaster.objects.filter(_employee_scope(self.request.user))
        )

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        is_manager = bool(employee.manager_id and employee.manager.user_id == self.request.user.id)
        if not _is_hr(self.request.user) and employee.user_id != self.request.user.id and not is_manager:
            raise PermissionDenied('Goals can only be created for yourself or a direct report.')
        serializer.save(created_by=self.request.user, status='draft')

    def perform_update(self, serializer):
        goal = self.get_object()
        if goal.status not in {'draft', 'pending'} and not _is_hr(self.request.user):
            raise ValidationError({'status': 'Active or completed goals are updated through check-ins.'})
        serializer.save(status=goal.status)

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            raise ValidationError({'status': 'Only draft goals can be deleted.'})
        if instance.employee.user_id != self.request.user.id and not _is_hr(self.request.user):
            raise PermissionDenied('Only the employee or HR can delete this goal.')
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        goal = self.get_object()
        if goal.status != 'draft':
            raise ValidationError({'status': 'Only draft goals can be submitted.'})
        goal.status = 'pending'
        goal.save(update_fields=['status', 'updated_at'])
        return Response(self.get_serializer(goal).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        goal = self.get_object()
        if not (_is_hr(request.user) or (goal.employee.manager_id and goal.employee.manager.user_id == request.user.id)):
            raise PermissionDenied('Only the employee manager or HR can approve goals.')
        total_weight = PerformanceGoal.objects.filter(
            employee=goal.employee, cycle=goal.cycle, status__in=['active', 'completed']
        ).exclude(pk=goal.pk).aggregate(total=models.Sum('weight'))['total'] or 0
        if total_weight + goal.weight > 100:
            raise ValidationError({'weight': 'Approved goal weights cannot exceed 100% for the cycle.'})
        goal.status = 'active'
        goal.approved_by = request.user
        goal.approved_at = timezone.now()
        goal.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        return Response(self.get_serializer(goal).data)


class GoalCheckInViewSet(viewsets.ModelViewSet):
    serializer_class = GoalCheckInSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['goal']

    def get_queryset(self):
        return GoalCheckIn.objects.select_related('goal__employee', 'created_by').filter(
            goal__employee__in=EmployeeMaster.objects.filter(_employee_scope(self.request.user))
        )

    def perform_create(self, serializer):
        goal = serializer.validated_data['goal']
        is_employee = goal.employee.user_id == self.request.user.id
        is_manager = bool(
            goal.employee.manager_id
            and goal.employee.manager.user_id == self.request.user.id
        )
        if not (_is_hr(self.request.user) or is_employee or is_manager):
            raise PermissionDenied('You cannot check in on this goal.')
        check_in = serializer.save(created_by=self.request.user)
        updates = {'progress': check_in.progress}
        if check_in.current_value is not None:
            updates['current_value'] = check_in.current_value
        if check_in.progress == 100:
            updates['status'] = 'completed'
        PerformanceGoal.objects.filter(pk=goal.pk).update(**updates)

    def perform_update(self, serializer):
        raise ValidationError({'detail': 'Goal check-ins are immutable; create a correction check-in.'})

    def perform_destroy(self, instance):
        raise ValidationError({'detail': 'Goal check-ins are retained as performance history.'})


class PerformanceReviewViewSet(viewsets.ModelViewSet):
    serializer_class = PerformanceReviewSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['employee', 'cycle', 'review_type', 'status', 'reviewer']

    def get_queryset(self):
        user = self.request.user
        queryset = PerformanceReview.objects.select_related('employee', 'cycle', 'reviewer')
        if _is_hr(user):
            return queryset
        return queryset.filter(
            Q(reviewer=user) | Q(employee__user=user, status__in=['submitted', 'acknowledged']) |
            Q(employee__manager__user=user)
        ).distinct()

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        reviewer = serializer.validated_data.get('reviewer') or self.request.user
        review_type = serializer.validated_data['review_type']
        can_assign = _is_hr(self.request.user) or (
            employee.manager_id and employee.manager.user_id == self.request.user.id
        )
        if reviewer != self.request.user and not can_assign:
            raise PermissionDenied('Only HR or the employee manager can assign a reviewer.')
        if review_type == 'self' and reviewer.id != employee.user_id:
            raise ValidationError({'reviewer': 'Self assessments must be assigned to the employee.'})
        serializer.save(reviewer=reviewer)

    def perform_update(self, serializer):
        if self.get_object().reviewer_id != self.request.user.id and not _is_hr(self.request.user):
            raise PermissionDenied('Only the assigned reviewer or HR can edit this review.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.reviewer_id != self.request.user.id and not _is_hr(self.request.user):
            raise PermissionDenied('Only the assigned reviewer or HR can delete this review.')
        if instance.status != 'draft':
            raise ValidationError({'status': 'Submitted reviews are retained as performance history.'})
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        review = self.get_object()
        if review.reviewer_id != request.user.id and not _is_hr(request.user):
            raise PermissionDenied('Only the assigned reviewer can submit this review.')
        if review.overall_score is None and review.goal_score is not None and review.competency_score is not None:
            review.overall_score = (
                review.goal_score * review.cycle.goal_weight +
                review.competency_score * review.cycle.competency_weight
            ) / 100
        if review.overall_score is None or not review.overall_comments.strip():
            raise ValidationError({'review': 'Overall score and comments are required before submission.'})
        review.status = 'submitted'
        review.submitted_at = timezone.now()
        review.save(update_fields=['status', 'overall_score', 'submitted_at', 'updated_at'])
        return Response(self.get_serializer(review).data)

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        review = self.get_object()
        if review.employee.user_id != request.user.id:
            raise PermissionDenied('Only the reviewed employee can acknowledge this review.')
        if review.status != 'submitted':
            raise ValidationError({'status': 'Only submitted reviews can be acknowledged.'})
        review.status = 'acknowledged'
        review.acknowledged_at = timezone.now()
        review.save(update_fields=['status', 'acknowledged_at', 'updated_at'])
        return Response(self.get_serializer(review).data)


class ContinuousFeedbackViewSet(viewsets.ModelViewSet):
    serializer_class = ContinuousFeedbackSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['employee', 'feedback_type', 'visibility', 'author']

    def get_queryset(self):
        user = self.request.user
        queryset = ContinuousFeedback.objects.select_related('employee', 'author', 'related_goal')
        if _is_hr(user):
            return queryset.filter(~Q(visibility='private') | Q(author=user))
        return queryset.filter(
            Q(author=user) | Q(employee__user=user, visibility='employee') |
            Q(employee__manager__user=user, visibility__in=['employee', 'management'])
        )

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_update(self, serializer):
        if self.get_object().author_id != self.request.user.id:
            raise PermissionDenied('Only the feedback author can edit it.')
        serializer.save(author=self.request.user)

    def perform_destroy(self, instance):
        if instance.author_id != self.request.user.id and not _is_hr(self.request.user):
            raise PermissionDenied('Only the feedback author or HR can remove it.')
        instance.delete()

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        feedback = self.get_object()
        if feedback.employee.user_id != request.user.id:
            raise PermissionDenied('Only the employee can acknowledge feedback.')
        feedback.acknowledged_at = timezone.now()
        feedback.save(update_fields=['acknowledged_at', 'updated_at'])
        return Response(self.get_serializer(feedback).data)


class DevelopmentPlanViewSet(viewsets.ModelViewSet):
    serializer_class = DevelopmentPlanSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['employee', 'cycle', 'status']

    def get_queryset(self):
        return DevelopmentPlan.objects.select_related('employee', 'cycle').prefetch_related('actions').filter(
            employee__in=EmployeeMaster.objects.filter(_employee_scope(self.request.user))
        )

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        if not EmployeeMaster.objects.filter(_employee_scope(self.request.user), pk=employee.pk).exists():
            raise PermissionDenied('You cannot create a development plan for this employee.')
        serializer.save(owner=self.request.user)


class DevelopmentActionViewSet(viewsets.ModelViewSet):
    serializer_class = DevelopmentActionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['plan', 'action_type', 'status']

    def get_queryset(self):
        return DevelopmentAction.objects.select_related('plan__employee').filter(
            plan__employee__in=EmployeeMaster.objects.filter(_employee_scope(self.request.user))
        )

    def perform_create(self, serializer):
        plan = serializer.validated_data['plan']
        if not self.get_queryset().filter(plan=plan).exists():
            raise PermissionDenied('You cannot add actions to this development plan.')
        serializer.save()


class HRRestrictedViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _is_hr(request.user):
            raise PermissionDenied('This talent-management function is restricted to HR.')


class TalentAssessmentViewSet(HRRestrictedViewSet):
    serializer_class = TalentAssessmentSerializer
    queryset = TalentAssessment.objects.select_related('employee', 'cycle', 'assessed_by').all()
    filterset_fields = ['employee', 'cycle', 'performance', 'potential', 'retention_risk', 'critical_role']

    def perform_create(self, serializer):
        serializer.save(assessed_by=self.request.user)

    @action(detail=False, methods=['get'])
    def matrix(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        cells = {
            f'P{performance}-T{potential}': queryset.filter(
                performance=performance, potential=potential
            ).count()
            for performance in (1, 2, 3) for potential in (1, 2, 3)
        }
        return Response({'total': queryset.count(), 'cells': cells})


class SuccessionPlanViewSet(HRRestrictedViewSet):
    serializer_class = SuccessionPlanSerializer
    queryset = SuccessionPlan.objects.select_related('incumbent').prefetch_related('candidates__employee').all()
    filterset_fields = ['department', 'criticality', 'status', 'incumbent']

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class SuccessionCandidateViewSet(HRRestrictedViewSet):
    serializer_class = SuccessionCandidateSerializer
    queryset = SuccessionCandidate.objects.select_related('plan', 'employee').all()
    filterset_fields = ['plan', 'employee', 'readiness']


class PromotionCaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PromotionCaseSerializer
    filterset_fields = ['employee', 'status']

    def get_queryset(self):
        queryset = PromotionCase.objects.select_related('employee', 'workflow_instance__current_stage')
        if _is_hr(self.request.user):
            return queryset
        return queryset.filter(Q(employee__user=self.request.user) | Q(employee__manager__user=self.request.user))

    def perform_create(self, serializer):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can open promotion cases.')
        case = serializer.save(requested_by=self.request.user, status='pending')
        workflow = HRWorkflowService.start(
            'promotion_case_v1', 'hr.promotion_case', case.pk,
            employee=case.employee, requested_by=self.request.user,
            context={'current_title': case.current_title, 'proposed_title': case.proposed_title},
        )
        case.workflow_instance = workflow
        case.save(update_fields=['workflow_instance', 'updated_at'])

    def perform_update(self, serializer):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can update promotion cases.')
        if self.get_object().status != 'draft':
            raise ValidationError({'status': 'A submitted promotion case cannot be edited.'})
        serializer.save()

    def perform_destroy(self, instance):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can delete promotion cases.')
        if instance.status != 'draft':
            raise ValidationError({'status': 'A submitted promotion case cannot be deleted.'})
        instance.delete()

    def _decide(self, request, decision):
        case = self.get_object()
        workflow = HRWorkflowService.decide(case.workflow_instance, request.user, decision, request.data.get('note', ''))
        case.status = workflow.status if workflow.status != 'pending' else 'pending'
        case.save(update_fields=['status', 'updated_at'])
        if workflow.status == 'approved' and case.effective_date and case.effective_date <= timezone.localdate():
            case.employee.designation = case.proposed_title
            case.employee.save(update_fields=['designation', 'updated_at'])
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._decide(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._decide(request, 'reject')


class WorkShiftViewSet(viewsets.ModelViewSet):
    serializer_class = WorkShiftSerializer
    permission_classes = [IsAuthenticated]
    queryset = WorkShift.objects.all()
    filterset_fields = ['is_active', 'crosses_midnight']

    def _require_hr(self):
        if not _is_hr(self.request.user):
            raise PermissionDenied('Only HR can manage shifts.')

    def perform_create(self, serializer):
        self._require_hr(); serializer.save()

    def perform_update(self, serializer):
        self._require_hr(); serializer.save()

    def perform_destroy(self, instance):
        self._require_hr(); instance.delete()


class ShiftRosterViewSet(viewsets.ModelViewSet):
    serializer_class = ShiftRosterSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['department', 'location', 'status', 'start_date', 'end_date']

    def get_queryset(self):
        queryset = ShiftRoster.objects.prefetch_related('assignments__employee', 'assignments__shift')
        if _is_hr(self.request.user):
            return queryset
        return queryset.none()

    def perform_create(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can create rosters.')
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can edit rosters.')
        if self.get_object().status == 'locked': raise ValidationError({'status': 'Locked rosters cannot be edited.'})
        serializer.save()

    def perform_destroy(self, instance):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can delete rosters.')
        if instance.status != 'draft': raise ValidationError({'status': 'Only draft rosters can be deleted.'})
        instance.delete()

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        if not _is_hr(request.user): raise PermissionDenied('Only HR can publish rosters.')
        roster = self.get_object()
        if not roster.assignments.exists(): raise ValidationError({'assignments': 'A roster must contain assignments before publishing.'})
        roster.status = 'published'; roster.published_at = timezone.now()
        roster.save(update_fields=['status', 'published_at', 'updated_at'])
        return Response(self.get_serializer(roster).data)


class ShiftAssignmentViewSet(viewsets.ModelViewSet):
    serializer_class = ShiftAssignmentSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['roster', 'employee', 'shift', 'date', 'status']

    def get_queryset(self):
        return ShiftAssignment.objects.select_related('roster', 'employee', 'shift').filter(
            employee__in=EmployeeMaster.objects.filter(_employee_scope(self.request.user))
        )

    def perform_create(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can assign shifts.')
        if serializer.validated_data['roster'].status != 'draft': raise ValidationError({'roster': 'Assignments can only be added to draft rosters.'})
        serializer.save()

    def perform_update(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can update assignments.')
        if self.get_object().roster.status == 'locked': raise ValidationError({'roster': 'Locked roster assignments cannot be changed.'})
        serializer.save()

    def perform_destroy(self, instance):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can delete shift assignments.')
        if instance.roster.status == 'locked': raise ValidationError({'roster': 'Locked roster assignments cannot be deleted.'})
        instance.delete()


class OvertimeRequestViewSet(viewsets.ModelViewSet):
    serializer_class = OvertimeRequestSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['employee', 'work_date', 'status']

    def get_queryset(self):
        return OvertimeRequest.objects.select_related('employee', 'assignment', 'workflow_instance__current_stage').filter(
            employee__in=EmployeeMaster.objects.filter(_employee_scope(self.request.user))
        )

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        if not _is_hr(self.request.user) and employee.user_id != self.request.user.id:
            raise PermissionDenied('Employees can only request overtime for themselves.')
        overtime = serializer.save(requested_by=self.request.user, status='pending')
        workflow = HRWorkflowService.start(
            'overtime_request_v1', 'hr.overtime_request', overtime.pk,
            employee=employee, requested_by=self.request.user,
            context={'work_date': str(overtime.work_date), 'requested_hours': str(overtime.requested_hours)},
        )
        overtime.workflow_instance = workflow
        overtime.save(update_fields=['workflow_instance', 'updated_at'])

    def perform_update(self, serializer):
        overtime = self.get_object()
        if overtime.status != 'draft' and not _is_hr(self.request.user):
            raise ValidationError({'status': 'Only draft overtime requests can be edited.'})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            raise ValidationError({'status': 'Only draft overtime requests can be deleted.'})
        if instance.employee.user_id != self.request.user.id and not _is_hr(self.request.user):
            raise PermissionDenied('You cannot delete this overtime request.')
        instance.delete()

    def _decide(self, request, decision):
        overtime = self.get_object()
        approved_hours = request.data.get('approved_hours', overtime.requested_hours)
        if decision == 'approve' and float(approved_hours) > float(overtime.requested_hours):
            raise ValidationError({'approved_hours': 'Approved hours cannot exceed requested hours.'})
        workflow = HRWorkflowService.decide(overtime.workflow_instance, request.user, decision, request.data.get('note', ''))
        overtime.status = workflow.status if workflow.status != 'pending' else 'pending'
        if workflow.status == 'approved':
            overtime.approved_hours = approved_hours
            overtime.reviewed_at = timezone.now()
        elif workflow.status == 'rejected':
            overtime.reviewed_at = timezone.now()
        overtime.save(update_fields=['status', 'approved_hours', 'reviewed_at', 'updated_at'])
        return Response(self.get_serializer(overtime).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._decide(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._decide(request, 'reject')

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        overtime = self.get_object()
        if overtime.employee.user_id != request.user.id and not _is_hr(request.user):
            raise PermissionDenied('Only the employee or HR can cancel overtime.')
        HRWorkflowService.cancel(overtime.workflow_instance, request.user, request.data.get('note', ''))
        overtime.status = 'cancelled'
        overtime.save(update_fields=['status', 'updated_at'])
        return Response(self.get_serializer(overtime).data)


class EmployeeServiceRequestViewSet(viewsets.ModelViewSet):
    serializer_class = EmployeeServiceRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['employee', 'request_type', 'status', 'priority']
    search_fields = ['request_number', 'title', 'description', 'destination', 'category']
    ordering_fields = ['created_at', 'priority', 'amount', 'start_date']

    def get_queryset(self):
        user = self.request.user
        queryset = EmployeeServiceRequest.objects.select_related(
            'employee__user', 'employee__manager__user',
            'workflow_instance__current_stage', 'assigned_to',
        ).prefetch_related('comments__author')
        if _is_hr(user):
            return queryset
        role_codes = _role_codes(user)
        return queryset.filter(
            Q(employee__user=user) |
            Q(employee__manager__user=user) |
            Q(assigned_to=user) |
            Q(workflow_instance__tasks__status='pending', workflow_instance__tasks__assigned_to=user) |
            Q(workflow_instance__tasks__status='pending', workflow_instance__tasks__assigned_role_code__in=role_codes)
        ).distinct()

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        if not _is_hr(self.request.user) and employee.user_id != self.request.user.id:
            raise PermissionDenied('Employees can only submit requests for themselves.')
        request_type = serializer.validated_data['request_type']
        service_request = serializer.save(
            requested_by=self.request.user, status='pending', submitted_at=timezone.now()
        )
        workflow = HRWorkflowService.start(
            f'{request_type}_request_v1', f'hr.{request_type}_request', service_request.pk,
            employee=employee, requested_by=self.request.user,
            context={
                'request_number': service_request.request_number,
                'title': service_request.title,
                'amount': str(service_request.amount or ''),
            },
        )
        service_request.workflow_instance = workflow
        service_request.save(update_fields=['workflow_instance', 'updated_at'])

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.status != 'draft':
            raise ValidationError({'status': 'Submitted requests cannot be edited.'})
        if instance.employee.user_id != self.request.user.id and not _is_hr(self.request.user):
            raise PermissionDenied('You cannot edit this request.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            raise ValidationError({'status': 'Only drafts can be deleted.'})
        instance.delete()

    def _decide(self, request, decision):
        service_request = self.get_object()
        workflow = HRWorkflowService.decide(
            service_request.workflow_instance, request.user, decision, request.data.get('note', '')
        )
        service_request.status = workflow.status if workflow.status != 'approved' else 'approved'
        service_request.save(update_fields=['status', 'updated_at'])
        return Response(self.get_serializer(service_request).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._decide(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._decide(request, 'reject')

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        service_request = self.get_object()
        if service_request.employee.user_id != request.user.id and not _is_hr(request.user):
            raise PermissionDenied('Only the employee or HR can cancel this request.')
        HRWorkflowService.cancel(service_request.workflow_instance, request.user, request.data.get('note', ''))
        service_request.status = 'cancelled'
        service_request.closed_at = timezone.now()
        service_request.save(update_fields=['status', 'closed_at', 'updated_at'])
        return Response(self.get_serializer(service_request).data)

    @action(detail=True, methods=['post'])
    def comment(self, request, pk=None):
        service_request = self.get_object()
        body = str(request.data.get('body', '')).strip()
        if not body:
            raise ValidationError({'body': 'Comment text is required.'})
        internal = bool(request.data.get('is_internal', False)) and _is_hr(request.user)
        service_request.comments.create(author=request.user, body=body, is_internal=internal)
        return Response(self.get_serializer(service_request).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        if not _is_hr(request.user):
            raise PermissionDenied('Only HR can close fulfilled requests.')
        service_request = self.get_object()
        if service_request.status != 'approved':
            raise ValidationError({'status': 'Only approved requests can be fulfilled.'})
        service_request.status = 'fulfilled'
        service_request.resolution = str(request.data.get('resolution', '')).strip()
        service_request.closed_at = timezone.now()
        service_request.save(update_fields=['status', 'resolution', 'closed_at', 'updated_at'])
        return Response(self.get_serializer(service_request).data)
