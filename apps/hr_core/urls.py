from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ContinuousFeedbackViewSet,
    DevelopmentActionViewSet,
    DevelopmentPlanViewSet,
    EmployeeIdentityAliasViewSet,
    EmployeeMasterViewSet,
    EmployeeServiceRequestViewSet,
    HRWorkflowDefinitionViewSet,
    HRWorkflowInstanceViewSet,
    HRWorkflowStageViewSet,
    GoalCheckInViewSet,
    OvertimeRequestViewSet,
    PerformanceCycleViewSet,
    PerformanceGoalViewSet,
    PerformanceReviewViewSet,
    PromotionCaseViewSet,
    ShiftAssignmentViewSet,
    ShiftRosterViewSet,
    SuccessionCandidateViewSet,
    SuccessionPlanViewSet,
    TalentAssessmentViewSet,
    WorkShiftViewSet,
)

router = DefaultRouter()
router.register(r'employees', EmployeeMasterViewSet, basename='hr-employee')
router.register(r'identity-aliases', EmployeeIdentityAliasViewSet, basename='hr-identity-alias')
router.register(r'workflow-definitions', HRWorkflowDefinitionViewSet, basename='hr-workflow-definition')
router.register(r'workflow-stages', HRWorkflowStageViewSet, basename='hr-workflow-stage')
router.register(r'workflows', HRWorkflowInstanceViewSet, basename='hr-workflow')
router.register(r'performance-cycles', PerformanceCycleViewSet, basename='hr-performance-cycle')
router.register(r'goals', PerformanceGoalViewSet, basename='hr-goal')
router.register(r'goal-check-ins', GoalCheckInViewSet, basename='hr-goal-check-in')
router.register(r'performance-reviews', PerformanceReviewViewSet, basename='hr-performance-review')
router.register(r'continuous-feedback', ContinuousFeedbackViewSet, basename='hr-continuous-feedback')
router.register(r'development-plans', DevelopmentPlanViewSet, basename='hr-development-plan')
router.register(r'development-actions', DevelopmentActionViewSet, basename='hr-development-action')
router.register(r'talent-assessments', TalentAssessmentViewSet, basename='hr-talent-assessment')
router.register(r'succession-plans', SuccessionPlanViewSet, basename='hr-succession-plan')
router.register(r'succession-candidates', SuccessionCandidateViewSet, basename='hr-succession-candidate')
router.register(r'promotion-cases', PromotionCaseViewSet, basename='hr-promotion-case')
router.register(r'work-shifts', WorkShiftViewSet, basename='hr-work-shift')
router.register(r'shift-rosters', ShiftRosterViewSet, basename='hr-shift-roster')
router.register(r'shift-assignments', ShiftAssignmentViewSet, basename='hr-shift-assignment')
router.register(r'overtime-requests', OvertimeRequestViewSet, basename='hr-overtime-request')
router.register(r'service-requests', EmployeeServiceRequestViewSet, basename='hr-service-request')

urlpatterns = [path('', include(router.urls))]
