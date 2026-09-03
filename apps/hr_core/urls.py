from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EmployeeIdentityAliasViewSet,
    EmployeeMasterViewSet,
    HRWorkflowDefinitionViewSet,
    HRWorkflowInstanceViewSet,
    HRWorkflowStageViewSet,
)

router = DefaultRouter()
router.register(r'employees', EmployeeMasterViewSet, basename='hr-employee')
router.register(r'identity-aliases', EmployeeIdentityAliasViewSet, basename='hr-identity-alias')
router.register(r'workflow-definitions', HRWorkflowDefinitionViewSet, basename='hr-workflow-definition')
router.register(r'workflow-stages', HRWorkflowStageViewSet, basename='hr-workflow-stage')
router.register(r'workflows', HRWorkflowInstanceViewSet, basename='hr-workflow')

urlpatterns = [path('', include(router.urls))]
