"""Canonical employee and reusable HR workflow APIs."""

from datetime import timedelta

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
    HRAssistantInteraction,
    HRAuditEvent,
    HRConsentRecord,
    HRPolicyDocument,
    HRPrivacyRequest,
    HRRetentionPolicy,
    MicrosoftGraphConnection,
    MicrosoftGraphUserLink,
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
    HRAssistantInteractionSerializer,
    HRAuditEventSerializer,
    HRConsentRecordSerializer,
    HRPolicyDocumentSerializer,
    HRPrivacyRequestSerializer,
    HRRetentionPolicySerializer,
    MicrosoftGraphConnectionSerializer,
    MicrosoftGraphUserLinkSerializer,
)
from .workflows import HRWorkflowService
from .assistant import accessible_policies, answer_question
from .governance import audit, employee_for_user, is_manager
from .microsoft_graph import GraphConfigurationError, MicrosoftGraphService


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


class SelfServiceWorkspaceViewSet(viewsets.ViewSet):
    """One permission-scoped entry point for employee and manager work."""
    permission_classes = [IsAuthenticated]

    def list(self, request):
        employee = employee_for_user(request.user)
        direct_reports = EmployeeMaster.objects.filter(manager=employee) if employee else EmployeeMaster.objects.none()
        manager = bool(direct_reports.exists())
        my = {
            'service_requests': EmployeeServiceRequest.objects.filter(employee=employee).exclude(status__in=['fulfilled', 'cancelled']).count() if employee else 0,
            'goals': PerformanceGoal.objects.filter(employee=employee, status__in=['pending', 'active']).count() if employee else 0,
            'reviews': PerformanceReview.objects.filter(employee=employee, status__in=['draft', 'reopened']).count() if employee else 0,
            'overtime': OvertimeRequest.objects.filter(employee=employee, status='pending').count() if employee else 0,
        }
        manager_queue = {'employees': 0, 'service_requests': 0, 'goals': 0, 'reviews': 0, 'overtime': 0}
        if manager:
            manager_queue = {
                'employees': direct_reports.count(),
                'service_requests': EmployeeServiceRequest.objects.filter(employee__in=direct_reports, status='pending').count(),
                'goals': PerformanceGoal.objects.filter(employee__in=direct_reports, status='pending').count(),
                'reviews': PerformanceReview.objects.filter(employee__in=direct_reports, reviewer=request.user, status__in=['draft', 'reopened']).count(),
                'overtime': OvertimeRequest.objects.filter(employee__in=direct_reports, status='pending').count(),
            }
        return Response({
            'employee': EmployeeMasterListSerializer(employee).data if employee else None,
            'capabilities': {'employee': bool(employee), 'manager': manager, 'hr': _is_hr(request.user)},
            'my_work': my, 'manager_queue': manager_queue,
        })


class MicrosoftGraphConnectionViewSet(viewsets.ModelViewSet):
    serializer_class = MicrosoftGraphConnectionSerializer
    permission_classes = [IsAuthenticated]
    queryset = MicrosoftGraphConnection.objects.all()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _is_hr(request.user):
            raise PermissionDenied('Only HR administrators can manage Microsoft Graph.')

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user, updated_by=self.request.user)
        audit(actor=self.request.user, action='graph.connection.create', object_type='MicrosoftGraphConnection', object_id=instance.pk, request=self.request)

    def perform_update(self, serializer):
        instance = serializer.save(updated_by=self.request.user)
        audit(actor=self.request.user, action='graph.connection.update', object_type='MicrosoftGraphConnection', object_id=instance.pk, request=self.request)

    def _run(self, request, operation):
        connection = self.get_object()
        service = MicrosoftGraphService(connection)
        try:
            result = getattr(service, operation)()
            audit(actor=request.user, action=f'graph.{operation}', object_type='MicrosoftGraphConnection', object_id=connection.pk, metadata=result, request=request)
            return Response(result)
        except (GraphConfigurationError, Exception) as exc:
            audit(actor=request.user, action=f'graph.{operation}', object_type='MicrosoftGraphConnection', object_id=connection.pk, outcome='failure', metadata={'error': str(exc)}, request=request)
            code = status.HTTP_400_BAD_REQUEST if isinstance(exc, GraphConfigurationError) else status.HTTP_502_BAD_GATEWAY
            return Response({'detail': str(exc)}, status=code)

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None): return self._run(request, 'health_check')

    @action(detail=True, methods=['post'], url_path='sync-entra')
    def sync_entra(self, request, pk=None): return self._run(request, 'sync_entra_users')

    @action(detail=True, methods=['post'], url_path='sync-sharepoint-policies')
    def sync_sharepoint_policies(self, request, pk=None): return self._run(request, 'sync_sharepoint_policies')

    @action(detail=True, methods=['post'], url_path='send-test-mail')
    def send_test_mail(self, request, pk=None):
        recipient = str(request.data.get('recipient', '')).strip()
        if not recipient: raise ValidationError({'recipient': 'Recipient email is required.'})
        result = MicrosoftGraphService(self.get_object()).send_mail(recipient, 'RADAI Microsoft Graph connection test', 'Microsoft Graph Outlook delivery is connected.')
        audit(actor=request.user, action='graph.outlook.test_mail', metadata={'recipient': recipient}, request=request)
        return Response(result)

    @action(detail=True, methods=['post'], url_path='send-test-teams')
    def send_test_teams(self, request, pk=None):
        recipient = str(request.data.get('recipient_entra_id', '')).strip()
        if not recipient: raise ValidationError({'recipient_entra_id': 'Recipient Entra object ID is required.'})
        result = MicrosoftGraphService(self.get_object()).send_teams_notification(recipient, 'RADAI Microsoft Graph connection test')
        audit(actor=request.user, action='graph.teams.test_notification', metadata={'recipient_entra_id': recipient}, request=request)
        return Response(result)


class MicrosoftGraphUserLinkViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MicrosoftGraphUserLinkSerializer
    permission_classes = [IsAuthenticated]
    queryset = MicrosoftGraphUserLink.objects.select_related('employee').all()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _is_hr(request.user): raise PermissionDenied('Only HR can view Entra links.')


class HRPolicyDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = HRPolicyDocumentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['category', 'jurisdiction', 'status', 'visibility']
    search_fields = ['title', 'category', 'content']

    def get_queryset(self):
        if _is_hr(self.request.user): return HRPolicyDocument.objects.all()
        accessible = accessible_policies(self.request.user)
        ids = [p.pk for p in accessible] if isinstance(accessible, list) else accessible.values_list('pk', flat=True)
        return HRPolicyDocument.objects.filter(pk__in=ids)

    def perform_create(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can create policies.')
        policy = serializer.save(owner=self.request.user)
        audit(actor=self.request.user, action='policy.create', object_type='HRPolicyDocument', object_id=policy.pk, request=self.request)

    def perform_update(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can edit policies.')
        policy = serializer.save()
        audit(actor=self.request.user, action='policy.update', object_type='HRPolicyDocument', object_id=policy.pk, request=self.request)

    def perform_destroy(self, instance):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can retire policies.')
        instance.status = 'retired'; instance.save(update_fields=['status', 'updated_at'])

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        if not _is_hr(request.user): raise PermissionDenied('Only HR can publish policies.')
        policy = self.get_object(); policy.status = 'published'; policy.published_at = timezone.now(); policy.save(update_fields=['status', 'published_at', 'updated_at'])
        audit(actor=request.user, action='policy.publish', object_type='HRPolicyDocument', object_id=policy.pk, request=request)
        return Response(self.get_serializer(policy).data)


class HRAssistantViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HRAssistantInteractionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return HRAssistantInteraction.objects.filter(user=self.request.user)

    @action(detail=False, methods=['post'])
    def ask(self, request):
        question = str(request.data.get('question', '')).strip()
        if len(question) < 3: raise ValidationError({'question': 'Please enter a complete HR question.'})
        if len(question) > 2000: raise ValidationError({'question': 'Question cannot exceed 2,000 characters.'})
        result = answer_question(request.user, question)
        employee = employee_for_user(request.user)
        interaction = HRAssistantInteraction.objects.create(user=request.user, employee=employee, question=question, **result)
        audit(actor=request.user, action='assistant.ask', object_type='HRAssistantInteraction', object_id=interaction.pk, employee=employee, metadata={'grounded': result['grounded'], 'citation_count': len(result['citations'])}, request=request)
        return Response(self.get_serializer(interaction).data, status=status.HTTP_201_CREATED)


class HRAuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HRAuditEventSerializer
    permission_classes = [IsAuthenticated]
    queryset = HRAuditEvent.objects.select_related('actor', 'employee').all()
    filterset_fields = ['action', 'outcome', 'object_type', 'employee']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _is_hr(request.user): raise PermissionDenied('Only HR can review the HR audit ledger.')


class HRConsentRecordViewSet(viewsets.ModelViewSet):
    serializer_class = HRConsentRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return HRConsentRecord.objects.all() if _is_hr(self.request.user) else HRConsentRecord.objects.filter(employee__user=self.request.user)

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        if not _is_hr(self.request.user) and employee.user_id != self.request.user.id: raise PermissionDenied('You can only record your own consent.')
        status_value = serializer.validated_data.get('status', 'granted')
        consent = serializer.save(recorded_by=self.request.user, granted_at=timezone.now() if status_value == 'granted' else None, withdrawn_at=timezone.now() if status_value == 'withdrawn' else None)
        audit(actor=self.request.user, action=f'consent.{status_value}', object_type='HRConsentRecord', object_id=consent.pk, employee=employee, request=self.request)


class HRPrivacyRequestViewSet(viewsets.ModelViewSet):
    serializer_class = HRPrivacyRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return HRPrivacyRequest.objects.all() if _is_hr(self.request.user) else HRPrivacyRequest.objects.filter(employee__user=self.request.user)

    def perform_create(self, serializer):
        employee = serializer.validated_data['employee']
        if not _is_hr(self.request.user) and employee.user_id != self.request.user.id: raise PermissionDenied('You can only submit your own privacy request.')
        item = serializer.save(due_at=timezone.now() + timedelta(days=30))
        audit(actor=self.request.user, action='privacy_request.submit', object_type='HRPrivacyRequest', object_id=item.pk, employee=employee, request=self.request)

    def perform_update(self, serializer):
        if not _is_hr(self.request.user): raise PermissionDenied('Only HR can process privacy requests.')
        serializer.save()

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        if not _is_hr(request.user): raise PermissionDenied('Only HR can complete privacy requests.')
        item = self.get_object(); item.status = 'completed'; item.resolution = str(request.data.get('resolution', '')).strip(); item.completed_at = timezone.now(); item.save(update_fields=['status', 'resolution', 'completed_at', 'updated_at'])
        audit(actor=request.user, action='privacy_request.complete', object_type='HRPrivacyRequest', object_id=item.pk, employee=item.employee, request=request)
        return Response(self.get_serializer(item).data)


class HRRetentionPolicyViewSet(viewsets.ModelViewSet):
    serializer_class = HRRetentionPolicySerializer
    permission_classes = [IsAuthenticated]
    queryset = HRRetentionPolicy.objects.all()

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _is_hr(request.user): raise PermissionDenied('Only HR can manage retention policies.')

    def perform_create(self, serializer): serializer.save(updated_by=self.request.user)
    def perform_update(self, serializer): serializer.save(updated_by=self.request.user)
