"""URL routes for Project Management — mounted at /api/v1/project-control/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BudgetAllocationViewSet,
    ChangeEventViewSet,
    CostSnapshotViewSet,
    CostAllocationViewSet,
    CostLedgerEntryViewSet,
    EstimateLineItemViewSet,
    EstimateViewSet,
    PlanningPackageViewSet,
    ProjectAnalyticsViewSet,
    ProjectDocumentViewSet,
    WBSNodeViewSet,
    phase_flags_view,
)

router = DefaultRouter()
router.register(r'estimates', EstimateViewSet, basename='project-control-estimate')
router.register(r'estimate-line-items', EstimateLineItemViewSet, basename='project-control-line-item')
router.register(r'wbs-nodes', WBSNodeViewSet, basename='project-control-wbs')
router.register(r'budget-allocations', BudgetAllocationViewSet, basename='project-control-budget-allocation')
router.register(r'cost-allocations', CostAllocationViewSet, basename='project-control-cost-allocation')
router.register(r'cost-ledger', CostLedgerEntryViewSet, basename='project-control-cost-ledger')
router.register(r'documents', ProjectDocumentViewSet, basename='project-control-document')
router.register(r'cost-snapshots', CostSnapshotViewSet, basename='project-control-snapshot')
router.register(r'change-events', ChangeEventViewSet, basename='project-control-change')
router.register(r'planning-packages', PlanningPackageViewSet, basename='project-control-planning-package')
router.register(r'analytics', ProjectAnalyticsViewSet, basename='project-control-analytics')

urlpatterns = [
    path('phase-flags/', phase_flags_view, name='project-control-phase-flags'),
    path('', include(router.urls)),
]
