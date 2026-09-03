"""
HR Core Admin - Django Admin Interface
"""
from django.contrib import admin
from apps.hr_core.models import (
    ContinuousFeedback,
    DevelopmentAction,
    DevelopmentPlan,
    EmployeeServiceRequest,
    EmployeeServiceRequestComment,
    EmployeeIdentityAlias,
    EmployeeMaster,
    HRWorkflowDefinition,
    HRWorkflowEvent,
    HRWorkflowInstance,
    HRWorkflowStage,
    HRWorkflowTask,
    GoalCheckIn,
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


@admin.register(EmployeeMaster)
class EmployeeMasterAdmin(admin.ModelAdmin):
    """Admin interface for Employee Master records."""
    
    list_display = [
        'employee_number',
        'get_full_name',
        'email',
        'department',
        'designation',
        'employment_status',
        'join_date',
        'branch',
    ]
    
    list_filter = [
        'employment_status',
        'branch',
        'department',
        'division',
        'join_date',
    ]
    
    search_fields = [
        'employee_number',
        'employee_code',
        'emp_code',
        'email',
        'first_name',
        'last_name',
    ]
    
    readonly_fields = [
        'id',
        'employee_number',
        'employee_code',
        'emp_code',
        'created_at',
        'updated_at',
        'photo_uploaded_at',
    ]
    
    fieldsets = (
        ('Identity', {
            'fields': (
                'id',
                'employee_number',
                'employee_code',
                'emp_code',
                'user',
                'email',
            )
        }),
        ('Personal Information', {
            'fields': (
                'first_name',
                'last_name',
                'preferred_given_name',
                'initials',
                'date_of_birth',
            )
        }),
        ('Photo', {
            'fields': (
                'photo_file_path',
                'photo_url',
                'photo_file_size',
                'photo_mime_type',
                'photo_uploaded_at',
            )
        }),
        ('Organization', {
            'fields': (
                'manager',
                'department',
                'division',
                'business_unit',
                'business_area',
                'office',
                'branch',
            )
        }),
        ('Job Information', {
            'fields': (
                'job_title_uae',
                'job_title_finland',
                'designation',
            )
        }),
        ('Employment', {
            'fields': (
                'join_date',
                'probation_end_date',
                'confirmation_date',
                'exit_date',
                'employment_status',
            )
        }),
        ('Salary', {
            'fields': (
                'current_base_salary',
                'currency',
            )
        }),
        ('Contact', {
            'fields': (
                'phone_number',
                'country',
                'city',
                'address',
                'postal_code',
            )
        }),
        ('Banking & Payroll', {
            'fields': (
                'bank_account_number',
                'bank_name',
                'iban',
                'swift_code',
            )
        }),
        ('Tax & Compliance', {
            'fields': (
                'pan_number',
                'uan_number',
                'tax_id',
            )
        }),
        ('System Integration', {
            'fields': (
                'employment_id',
                'candidate_id',
                'account_name',
            )
        }),
        ('Flags & Metadata', {
            'fields': (
                'is_test_person',
                'protected_identity',
                'not_signed',
                'created_by',
                'last_updated_by',
                'created_at',
                'updated_at',
            )
        }),
    )
    
    def get_full_name(self, obj):
        """Display full name."""
        return obj.get_full_name()
    get_full_name.short_description = 'Full Name'


@admin.register(EmployeeIdentityAlias)
class EmployeeIdentityAliasAdmin(admin.ModelAdmin):
    list_display = ('employee', 'source', 'identifier_type', 'value', 'is_primary', 'verified_at')
    list_filter = ('source', 'identifier_type', 'is_primary')
    search_fields = ('value', 'normalized_value', 'employee__email', 'employee__employee_number')
    readonly_fields = ('normalized_value', 'verified_at', 'created_at', 'updated_at')


class HRWorkflowStageInline(admin.TabularInline):
    model = HRWorkflowStage
    extra = 0
    ordering = ('sequence',)


@admin.register(HRWorkflowDefinition)
class HRWorkflowDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'version', 'subject_type', 'is_active')
    list_filter = ('is_active', 'subject_type')
    search_fields = ('code', 'name')
    inlines = (HRWorkflowStageInline,)


@admin.register(HRWorkflowInstance)
class HRWorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ('definition', 'subject_id', 'employee', 'status', 'current_stage', 'created_at')
    list_filter = ('status', 'definition')
    search_fields = ('subject_id', 'employee__email', 'employee__employee_number')
    readonly_fields = ('created_at', 'updated_at', 'completed_at')


@admin.register(HRWorkflowTask)
class HRWorkflowTaskAdmin(admin.ModelAdmin):
    list_display = ('instance', 'stage', 'assigned_to', 'assigned_role_code', 'status', 'due_at')
    list_filter = ('status', 'assigned_role_code')
    readonly_fields = ('created_at', 'updated_at', 'decided_at', 'reminder_sent_at', 'escalated_at')


@admin.register(HRWorkflowEvent)
class HRWorkflowEventAdmin(admin.ModelAdmin):
    list_display = ('instance', 'event_type', 'stage_code', 'actor', 'created_at')
    list_filter = ('event_type',)
    readonly_fields = ('instance', 'event_type', 'actor', 'stage_code', 'note', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PerformanceCycle)
class PerformanceCycleAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'status', 'goal_weight', 'competency_weight')
    list_filter = ('status',)


@admin.register(PerformanceGoal)
class PerformanceGoalAdmin(admin.ModelAdmin):
    list_display = ('title', 'employee', 'cycle', 'goal_type', 'weight', 'progress', 'status')
    list_filter = ('cycle', 'goal_type', 'status')
    search_fields = ('title', 'employee__email', 'employee__employee_number')


admin.site.register(GoalCheckIn)


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = ('employee', 'cycle', 'review_type', 'reviewer', 'overall_score', 'status')
    list_filter = ('cycle', 'review_type', 'status')


admin.site.register(ContinuousFeedback)


class DevelopmentActionInline(admin.TabularInline):
    model = DevelopmentAction
    extra = 0


@admin.register(DevelopmentPlan)
class DevelopmentPlanAdmin(admin.ModelAdmin):
    list_display = ('title', 'employee', 'target_role', 'status', 'target_date')
    list_filter = ('status',)
    inlines = (DevelopmentActionInline,)


@admin.register(TalentAssessment)
class TalentAssessmentAdmin(admin.ModelAdmin):
    list_display = ('employee', 'cycle', 'performance', 'potential', 'retention_risk', 'readiness', 'critical_role')
    list_filter = ('cycle', 'performance', 'potential', 'retention_risk', 'critical_role')


class SuccessionCandidateInline(admin.TabularInline):
    model = SuccessionCandidate
    extra = 0


@admin.register(SuccessionPlan)
class SuccessionPlanAdmin(admin.ModelAdmin):
    list_display = ('role_title', 'department', 'incumbent', 'criticality', 'status')
    list_filter = ('criticality', 'status', 'department')
    inlines = (SuccessionCandidateInline,)


@admin.register(PromotionCase)
class PromotionCaseAdmin(admin.ModelAdmin):
    list_display = ('employee', 'current_title', 'proposed_title', 'effective_date', 'status')
    list_filter = ('status', 'effective_date')


@admin.register(WorkShift)
class WorkShiftAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'start_time', 'end_time', 'crosses_midnight', 'is_active')
    list_filter = ('is_active', 'crosses_midnight')


class ShiftAssignmentInline(admin.TabularInline):
    model = ShiftAssignment
    extra = 0


@admin.register(ShiftRoster)
class ShiftRosterAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'location', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'department', 'location')
    inlines = (ShiftAssignmentInline,)


@admin.register(OvertimeRequest)
class OvertimeRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'work_date', 'requested_hours', 'approved_hours', 'status')
    list_filter = ('status', 'work_date')


class EmployeeServiceRequestCommentInline(admin.TabularInline):
    model = EmployeeServiceRequestComment
    extra = 0


@admin.register(EmployeeServiceRequest)
class EmployeeServiceRequestAdmin(admin.ModelAdmin):
    list_display = ('request_number', 'request_type', 'employee', 'priority', 'status', 'created_at')
    list_filter = ('request_type', 'status', 'priority')
    search_fields = ('request_number', 'title', 'employee__email')
    inlines = (EmployeeServiceRequestCommentInline,)
