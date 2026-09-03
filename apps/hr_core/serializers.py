"""
HR Core Serializers - Employee Master API Serialization
"""
from rest_framework import serializers
from apps.hr_core.models import (
    ContinuousFeedback,
    DevelopmentAction,
    DevelopmentPlan,
    EmployeeServiceRequest,
    EmployeeServiceRequestComment,
    EmployeeIdentityAlias,
    EmployeeMaster,
    GoalCheckIn,
    HRWorkflowDefinition,
    HRWorkflowEvent,
    HRWorkflowInstance,
    HRWorkflowStage,
    HRWorkflowTask,
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
from apps.users.serializers import UserSerializer


class EmployeeMasterListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for employee lists.
    Used in dropdowns, search results, etc.
    """
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    class Meta:
        model = EmployeeMaster
        fields = [
            'id',
            'employee_number',
            'employee_code',
            'email',
            'full_name',
            'display_name',
            'department',
            'designation',
            'employment_status',
            'photo_url',
        ]
        read_only_fields = fields


class EmployeeMasterDetailSerializer(serializers.ModelSerializer):
    """
    Full employee details serializer.
    Used for employee profile, onboarding, etc.
    """
    user = UserSerializer(read_only=True)
    manager = EmployeeMasterListSerializer(read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    # Photo upload field (write-only)
    photo = serializers.ImageField(write_only=True, required=False)
    
    class Meta:
        model = EmployeeMaster
        fields = [
            # Identity
            'id',
            'employee_number',
            'employee_code',
            'emp_code',
            'email',
            
            # Relations
            'user',
            'manager',
            
            # Personal
            'first_name',
            'last_name',
            'preferred_given_name',
            'initials',
            'full_name',
            'display_name',
            'date_of_birth',
            
            # Photo
            'photo',  # write-only
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            
            # Organization
            'department',
            'division',
            'business_unit',
            'business_area',
            'office',
            'branch',
            
            # Job
            'job_title_uae',
            'job_title_finland',
            'designation',
            
            # Employment
            'join_date',
            'probation_end_date',
            'confirmation_date',
            'exit_date',
            'employment_status',
            
            # Salary
            'current_base_salary',
            'currency',
            
            # Contact
            'phone_number',
            'country',
            'city',
            'address',
            'postal_code',
            
            # Banking
            'bank_account_number',
            'bank_name',
            'iban',
            'swift_code',
            
            # Tax
            'pan_number',
            'uan_number',
            'tax_id',
            
            # Integration
            'employment_id',
            'candidate_id',
            'account_name',
            
            # Flags
            'is_test_person',
            'protected_identity',
            'not_signed',
            
            # Metadata
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'employee_number',
            'employee_code',
            'emp_code',
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            'created_at',
            'updated_at',
        ]
    
    def create(self, validated_data):
        """Not used - employee creation goes through EmployeeService."""
        raise NotImplementedError("Use EmployeeService.create_employee() instead")
    
    def update(self, instance, validated_data):
        """Update employee, handling photo upload if present."""
        photo = validated_data.pop('photo', None)
        
        # Update all other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        
        # Handle photo upload separately through service
        if photo:
            from apps.hr_core.services import EmployeeService
            request_user = self.context.get('request').user if self.context.get('request') else None
            EmployeeService.upload_employee_photo(instance, photo, uploaded_by=request_user)
        
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        can_view_sensitive = bool(user and (user.is_staff or user.is_superuser or user.id == instance.user_id))
        if user and not can_view_sensitive:
            try:
                roles = set(user.rbac_profile.roles.filter(is_active=True).values_list('code', flat=True))
                can_view_sensitive = bool(roles & {'hr_manager', 'hr_admin', 'payroll_admin', 'finance_manager'})
            except Exception:
                pass
        if not can_view_sensitive:
            for field in (
                'current_base_salary', 'currency', 'bank_account_number', 'bank_name',
                'iban', 'swift_code', 'pan_number', 'uan_number', 'tax_id',
            ):
                data.pop(field, None)
        return data


class EmployeeIdentityAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeIdentityAlias
        fields = [
            'id', 'employee', 'source', 'identifier_type', 'value',
            'is_primary', 'verified_at', 'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class HRWorkflowStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRWorkflowStage
        fields = [
            'id', 'definition', 'code', 'name', 'sequence', 'approver_type',
            'approver_value', 'due_after_hours', 'escalate_after_hours',
            'escalation_role_code', 'require_comment_on_reject', 'configuration',
        ]


class HRWorkflowDefinitionSerializer(serializers.ModelSerializer):
    stages = HRWorkflowStageSerializer(many=True, read_only=True)

    class Meta:
        model = HRWorkflowDefinition
        fields = [
            'id', 'code', 'name', 'version', 'description', 'subject_type',
            'is_active', 'configuration', 'stages', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class HRWorkflowTaskSerializer(serializers.ModelSerializer):
    stage_name = serializers.CharField(source='stage.name', read_only=True)
    stage_code = serializers.CharField(source='stage.code', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    can_act = serializers.SerializerMethodField()

    class Meta:
        model = HRWorkflowTask
        fields = [
            'id', 'stage', 'stage_name', 'stage_code', 'assigned_to',
            'assigned_to_name', 'assigned_role_code', 'status', 'due_at',
            'reminder_sent_at', 'escalated_at', 'decided_by', 'decided_by_name',
            'decided_at', 'decision_note', 'can_act', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    @staticmethod
    def _name(user):
        return (user.get_full_name().strip() or user.email) if user else None

    def get_assigned_to_name(self, obj):
        return self._name(obj.assigned_to)

    def get_decided_by_name(self, obj):
        return self._name(obj.decided_by)

    def get_can_act(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        from .workflows import HRWorkflowService

        return obj.status == 'pending' and HRWorkflowService.can_act(obj, request.user)


class HRWorkflowEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = HRWorkflowEvent
        fields = [
            'id', 'event_type', 'actor', 'actor_name', 'stage_code',
            'note', 'metadata', 'created_at',
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        return (obj.actor.get_full_name().strip() or obj.actor.email) if obj.actor else None


class HRWorkflowInstanceSerializer(serializers.ModelSerializer):
    definition_code = serializers.CharField(source='definition.code', read_only=True)
    definition_name = serializers.CharField(source='definition.name', read_only=True)
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    current_stage_name = serializers.CharField(source='current_stage.name', read_only=True)
    tasks = HRWorkflowTaskSerializer(many=True, read_only=True)
    events = HRWorkflowEventSerializer(many=True, read_only=True)

    class Meta:
        model = HRWorkflowInstance
        fields = [
            'id', 'definition', 'definition_code', 'definition_name', 'employee',
            'employee_name', 'subject_type', 'subject_id', 'status', 'current_stage',
            'current_stage_name', 'requested_by', 'completed_at', 'context',
            'tasks', 'events', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class PerformanceCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerformanceCycle
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        start = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        end = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        if start and end and end < start:
            raise serializers.ValidationError({'end_date': 'End date must be on or after start date.'})
        goal_weight = attrs.get('goal_weight', getattr(self.instance, 'goal_weight', 60))
        competency_weight = attrs.get('competency_weight', getattr(self.instance, 'competency_weight', 40))
        if goal_weight + competency_weight != 100:
            raise serializers.ValidationError('Goal and competency weights must total 100%.')
        return attrs


class GoalCheckInSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = GoalCheckIn
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def validate_progress(self, value):
        if value > 100:
            raise serializers.ValidationError('Progress cannot exceed 100%.')
        return value

    def get_created_by_name(self, obj):
        return (obj.created_by.get_full_name().strip() or obj.created_by.email) if obj.created_by else None


class PerformanceGoalSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    cycle_name = serializers.CharField(source='cycle.name', read_only=True)
    check_ins = GoalCheckInSerializer(many=True, read_only=True)

    class Meta:
        model = PerformanceGoal
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'approved_by', 'approved_at', 'created_at', 'updated_at']

    def validate_progress(self, value):
        if value > 100:
            raise serializers.ValidationError('Progress cannot exceed 100%.')
        return value

    def validate_weight(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError('Weight must be between 0 and 100.')
        return value

    def validate(self, attrs):
        cycle = attrs.get('cycle', getattr(self.instance, 'cycle', None))
        due_date = attrs.get('due_date', getattr(self.instance, 'due_date', None))
        parent = attrs.get('parent_goal', getattr(self.instance, 'parent_goal', None))
        if cycle and due_date and not cycle.start_date <= due_date <= cycle.end_date:
            raise serializers.ValidationError({'due_date': 'Goal due date must fall inside the performance cycle.'})
        if parent and cycle and parent.cycle_id != cycle.id:
            raise serializers.ValidationError({'parent_goal': 'Aligned goals must belong to the same cycle.'})
        return attrs


class PerformanceReviewSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    reviewer_name = serializers.SerializerMethodField()
    cycle_name = serializers.CharField(source='cycle.name', read_only=True)

    class Meta:
        model = PerformanceReview
        fields = '__all__'
        read_only_fields = ['id', 'submitted_at', 'acknowledged_at', 'created_at', 'updated_at']
        extra_kwargs = {'reviewer': {'required': False}}

    def get_reviewer_name(self, obj):
        return obj.reviewer.get_full_name().strip() or obj.reviewer.email

    def validate(self, attrs):
        for field in ('goal_score', 'competency_score', 'overall_score'):
            value = attrs.get(field, getattr(self.instance, field, None))
            if value is not None and not 0 <= value <= 5:
                raise serializers.ValidationError({field: 'Score must be between 0 and 5.'})
        return attrs


class ContinuousFeedbackSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = ContinuousFeedback
        fields = '__all__'
        read_only_fields = ['id', 'author', 'acknowledged_at', 'created_at', 'updated_at']

    def get_author_name(self, obj):
        return obj.author.get_full_name().strip() or obj.author.email


class DevelopmentActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DevelopmentAction
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class DevelopmentPlanSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    actions = DevelopmentActionSerializer(many=True, read_only=True)

    class Meta:
        model = DevelopmentPlan
        fields = '__all__'
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']


class TalentAssessmentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    nine_box = serializers.SerializerMethodField()

    class Meta:
        model = TalentAssessment
        fields = '__all__'
        read_only_fields = ['id', 'assessed_by', 'created_at', 'updated_at']

    def get_nine_box(self, obj):
        return f'P{obj.performance}-T{obj.potential}'


class SuccessionCandidateSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)

    class Meta:
        model = SuccessionCandidate
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class SuccessionPlanSerializer(serializers.ModelSerializer):
    incumbent_name = serializers.CharField(source='incumbent.get_display_name', read_only=True)
    candidates = SuccessionCandidateSerializer(many=True, read_only=True)

    class Meta:
        model = SuccessionPlan
        fields = '__all__'
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']


class PromotionCaseSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    workflow_status = serializers.CharField(source='workflow_instance.status', read_only=True)

    class Meta:
        model = PromotionCase
        fields = '__all__'
        read_only_fields = ['id', 'status', 'requested_by', 'workflow_instance', 'created_at', 'updated_at']


class WorkShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkShift
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ShiftAssignmentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    shift_name = serializers.CharField(source='shift.name', read_only=True)
    shift_code = serializers.CharField(source='shift.code', read_only=True)

    class Meta:
        model = ShiftAssignment
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        roster = attrs.get('roster', getattr(self.instance, 'roster', None))
        date = attrs.get('date', getattr(self.instance, 'date', None))
        if roster and date and not roster.start_date <= date <= roster.end_date:
            raise serializers.ValidationError({'date': 'Assignment date must fall inside the roster period.'})
        return attrs


class ShiftRosterSerializer(serializers.ModelSerializer):
    assignments = ShiftAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = ShiftRoster
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'published_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        start = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        end = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        if start and end and end < start:
            raise serializers.ValidationError({'end_date': 'End date must be on or after start date.'})
        return attrs


class OvertimeRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    workflow_status = serializers.CharField(source='workflow_instance.status', read_only=True)
    current_stage = serializers.CharField(source='workflow_instance.current_stage.name', read_only=True)

    class Meta:
        model = OvertimeRequest
        fields = '__all__'
        read_only_fields = ['id', 'status', 'requested_by', 'workflow_instance', 'reviewed_at', 'created_at', 'updated_at']

    def validate_requested_hours(self, value):
        if value <= 0 or value > 24:
            raise serializers.ValidationError('Requested overtime must be greater than 0 and no more than 24 hours.')
        return value

    def validate(self, attrs):
        assignment = attrs.get('assignment', getattr(self.instance, 'assignment', None))
        employee = attrs.get('employee', getattr(self.instance, 'employee', None))
        work_date = attrs.get('work_date', getattr(self.instance, 'work_date', None))
        if assignment and (assignment.employee_id != employee.id or assignment.date != work_date):
            raise serializers.ValidationError({'assignment': 'Shift assignment must match the employee and work date.'})
        return attrs


class EmployeeServiceRequestCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeServiceRequestComment
        fields = '__all__'
        read_only_fields = ['id', 'request', 'author', 'created_at']

    def get_author_name(self, obj):
        return obj.author.get_full_name() or obj.author.username if obj.author else 'System'


class EmployeeServiceRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    current_stage = serializers.CharField(source='workflow_instance.current_stage.name', read_only=True)
    comments = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeServiceRequest
        fields = '__all__'
        read_only_fields = [
            'id', 'request_number', 'status', 'requested_by', 'assigned_to',
            'workflow_instance', 'submitted_at', 'closed_at', 'resolution',
            'created_at', 'updated_at',
        ]

    def get_comments(self, obj):
        request = self.context.get('request')
        queryset = obj.comments.all()
        if request and not (request.user.is_staff or request.user.is_superuser):
            queryset = queryset.filter(is_internal=False)
        return EmployeeServiceRequestCommentSerializer(queryset, many=True).data

    def get_is_owner(self, obj):
        request = self.context.get('request')
        return bool(request and obj.employee.user_id == request.user.id)

    def get_can_approve(self, obj):
        request = self.context.get('request')
        if not request or not obj.workflow_instance_id or obj.status != 'pending':
            return False
        from .workflows import HRWorkflowService
        task = obj.workflow_instance.tasks.filter(status='pending', stage=obj.workflow_instance.current_stage).first()
        return bool(task and HRWorkflowService.can_act(task, request.user))

    def validate(self, attrs):
        request_type = attrs.get('request_type', getattr(self.instance, 'request_type', None))
        amount = attrs.get('amount', getattr(self.instance, 'amount', None))
        start = attrs.get('start_date', getattr(self.instance, 'start_date', None))
        end = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        if request_type == 'expense' and (amount is None or amount <= 0):
            raise serializers.ValidationError({'amount': 'Expense requests require a positive amount.'})
        if request_type == 'travel':
            if not start or not end or not attrs.get('destination', getattr(self.instance, 'destination', '')):
                raise serializers.ValidationError({'travel': 'Travel dates and destination are required.'})
            if end < start:
                raise serializers.ValidationError({'end_date': 'End date cannot be before start date.'})
        return attrs
