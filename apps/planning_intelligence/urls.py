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
    path('', include(router.urls)),
]
