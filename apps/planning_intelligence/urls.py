"""URL routes for the RADAI Project Planning Application — mounted at
/api/v1/planning-intelligence/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    PlanningAuditEventViewSet, PlanningFileViewSet, PlanningGenerationViewSet,
    PlanningJobViewSet, PlanningProjectViewSet,
)
from .schedule_views import (
    ActivityAssignmentViewSet, ActivityRelationshipViewSet, CalendarExceptionViewSet,
    DailyFieldUpdateViewSet,
    ScheduleActivityViewSet, ScheduleBaselineViewSet, ScheduleCalculationRunViewSet,
    ScheduleResourceViewSet, ScheduleVersionViewSet, ScheduleViewSet,
    ScheduleWBSNodeViewSet, WorkCalendarViewSet,
)
from .intelligence_views import (
    BasisDeliverableViewSet, DocumentAuthorityRuleViewSet, DocumentIntelligenceRunViewSet,
    DocumentProfileViewSet, IntelligenceConflictViewSet, IntelligenceFactViewSet,
    GenerationDependencyViewSet, GenerationPlanViewSet, PlanDeliverableViewSet, ScheduleBasisViewSet,
)
from .enterprise_views import (
    IntegrationDeliveryViewSet, IntegrationEndpointViewSet, PlanningEnterpriseViewSet,
    ScheduleExportRecordViewSet,
)
from .proposal_views import ProposalExportRecordViewSet, TechnicalProposalViewSet
from .project_setup_views import ProjectSetupAISettingsView, ProjectSetupCreateView, ProjectSetupOptionsView, ProjectSetupPreviewView
from .simple_planning_views import SimplePlanningView
from .evidence_views import EvidenceReviewView
from .agreement_views import AgreementWorkspaceView
from .planning_profile_views import PlanningProfileView
from .planning_build_views import PlanningBuildView, PlanningRiskView
from .operational_control_views import OperationalControlsView
from .delay_views import DelayAnalysisView, DelayCaseExportView
from .workflow_views import (
    EngineeringDependencyTemplateViewSet, ProjectScheduleConfigurationViewSet,
    ScheduleDefaultProposalViewSet, WorkflowTemplateOverrideViewSet, WorkflowTemplateViewSet,
)

router = DefaultRouter()
router.register(r'projects', PlanningProjectViewSet, basename='planning-project')
router.register(r'files', PlanningFileViewSet, basename='planning-file')
router.register(r'generations', PlanningGenerationViewSet, basename='planning-generation')
router.register(r'jobs', PlanningJobViewSet, basename='planning-job')
router.register(r'audit-events', PlanningAuditEventViewSet, basename='planning-audit-event')
router.register(r'calendars', WorkCalendarViewSet, basename='work-calendar')
router.register(r'calendar-exceptions', CalendarExceptionViewSet, basename='calendar-exception')
router.register(r'schedules', ScheduleViewSet, basename='schedule')
router.register(r'schedule-versions', ScheduleVersionViewSet, basename='schedule-version')
router.register(r'wbs-nodes', ScheduleWBSNodeViewSet, basename='schedule-wbs-node')
router.register(r'activities', ScheduleActivityViewSet, basename='schedule-activity')
router.register(r'relationships', ActivityRelationshipViewSet, basename='activity-relationship')
router.register(r'resources', ScheduleResourceViewSet, basename='schedule-resource')
router.register(r'assignments', ActivityAssignmentViewSet, basename='activity-assignment')
router.register(r'daily-field-updates', DailyFieldUpdateViewSet, basename='daily-field-update')
router.register(r'baselines', ScheduleBaselineViewSet, basename='schedule-baseline')
router.register(r'calculation-runs', ScheduleCalculationRunViewSet, basename='schedule-calculation-run')
router.register(r'document-profiles', DocumentProfileViewSet, basename='document-profile')
router.register(r'intelligence-runs', DocumentIntelligenceRunViewSet, basename='document-intelligence-run')
router.register(r'intelligence-facts', IntelligenceFactViewSet, basename='intelligence-fact')
router.register(r'intelligence-conflicts', IntelligenceConflictViewSet, basename='intelligence-conflict')
router.register(r'document-authority-rules', DocumentAuthorityRuleViewSet, basename='document-authority-rule')
router.register(r'schedule-bases', ScheduleBasisViewSet, basename='schedule-basis')
router.register(r'basis-deliverables', BasisDeliverableViewSet, basename='basis-deliverable')
router.register(r'generation-plans', GenerationPlanViewSet, basename='generation-plan')
router.register(r'plan-deliverables', PlanDeliverableViewSet, basename='plan-deliverable')
router.register(r'generation-dependencies', GenerationDependencyViewSet, basename='generation-dependency')
router.register(r'integration-endpoints', IntegrationEndpointViewSet, basename='integration-endpoint')
router.register(r'integration-deliveries', IntegrationDeliveryViewSet, basename='integration-delivery')
router.register(r'schedule-export-records', ScheduleExportRecordViewSet, basename='schedule-export-record')
router.register(r'enterprise', PlanningEnterpriseViewSet, basename='planning-enterprise')
router.register(r'technical-proposals', TechnicalProposalViewSet, basename='technical-proposal')
router.register(r'proposal-export-records', ProposalExportRecordViewSet, basename='proposal-export-record')
router.register(r'workflow-templates', WorkflowTemplateViewSet, basename='workflow-template')
router.register(r'dependency-templates', EngineeringDependencyTemplateViewSet, basename='dependency-template')
router.register(r'schedule-configurations', ProjectScheduleConfigurationViewSet, basename='schedule-configuration')
router.register(r'schedule-default-proposals', ScheduleDefaultProposalViewSet, basename='schedule-default-proposal')
router.register(r'workflow-overrides', WorkflowTemplateOverrideViewSet, basename='workflow-override')

app_name = 'planning_intelligence'

urlpatterns = [
    path('agreement-workspaces/create/', AgreementWorkspaceView.as_view(operation='create'), name='agreement-workspace-create'),
    path('agreement-workspaces/projects/<int:project_id>/', AgreementWorkspaceView.as_view(), name='agreement-workspace'),
    *[path(f'agreement-workspaces/projects/<int:project_id>/{operation}/',
           AgreementWorkspaceView.as_view(operation=operation), name=f'agreement-workspace-{operation}')
      for operation in ('analyze', 'accept')],
    path('projects/<int:project_id>/delay-analysis/', DelayAnalysisView.as_view(), name='delay-analysis'),
    path('projects/<int:project_id>/delay-analysis/cases/<int:case_id>/export/', DelayCaseExportView.as_view(), name='delay-case-export'),
    path('projects/<int:project_id>/operational-controls/', OperationalControlsView.as_view(), name='operational-controls'),
    path('projects/<int:project_id>/planning-builds/', PlanningBuildView.as_view(), name='planning-builds'),
    path('projects/<int:project_id>/planning-builds/<uuid:build_id>/', PlanningBuildView.as_view(), name='planning-build-detail'),
    path('projects/<int:project_id>/planning-builds/<uuid:build_id>/apply/', PlanningBuildView.as_view(operation='apply'), name='planning-build-apply'),
    path('projects/<int:project_id>/risk-register/', PlanningRiskView.as_view(), name='planning-risk-register'),
    path('projects/<int:project_id>/planning-profiles/', PlanningProfileView.as_view(), name='planning-profiles'),
    path('projects/<int:project_id>/planning-profiles/select/', PlanningProfileView.as_view(operation='select'), name='planning-profile-select'),
    path('projects/<int:project_id>/planning-profiles/<int:profile_id>/', PlanningProfileView.as_view(), name='planning-profile-detail'),
    *[path(f'projects/<int:project_id>/planning-profiles/<int:profile_id>/{operation}/',
           PlanningProfileView.as_view(operation=operation), name=f'planning-profile-{operation}')
      for operation in ('propose', 'approve', 'reject', 'revise')],
    path('projects/<int:project_id>/evidence-review/', EvidenceReviewView.as_view(), name='evidence-review'),
    *[path(f'projects/<int:project_id>/evidence-review/{operation}/',
           EvidenceReviewView.as_view(operation=operation), name=f'evidence-review-{operation}')
      for operation in ('refresh', 'decisions', 'accepted-plan', 'materialize', 'bulk')],
    path('projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view(), name='simple-plan'),
    *[path(f'projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation), name=f'simple-plan-{operation}')
      for operation in ('analyse', 'submit', 'approve-publish', 'reopen', 'edit-activity', 'edit-row', 'confirm-parallel-logic', 'propose-schedule', 'apply-schedule', 'select-version', 'calculate', 'validate', 'source-preview', 'preview-source-import', 'apply-source-import', 'preview-source-logic', 'apply-source-logic', 'propose-intelligent-sequence', 'apply-intelligent-sequence')],
    path('project-setup/ai-settings/', ProjectSetupAISettingsView.as_view(), name='project-setup-ai-settings'),
    path('project-setup/options/', ProjectSetupOptionsView.as_view(), name='project-setup-options'),
    path('project-setup/preview/', ProjectSetupPreviewView.as_view(), name='project-setup-preview'),
    path('project-setup/create/', ProjectSetupCreateView.as_view(), name='project-setup-create'),
    path('', include(router.urls)),
]
