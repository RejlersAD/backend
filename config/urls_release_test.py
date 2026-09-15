"""Functional routes; module/action authorization has a separate guarded suite."""
from django.urls import include, path

from .urls_rbac_test import urlpatterns as rbac_patterns

urlpatterns = [
    *rbac_patterns,
    path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls')),
    path('api/v1/project-control/', include('apps.project_control.urls')),
    path('api/v1/procurement/', include('apps.procurement.urls')),
    path('api/v1/spec-customization/', include('apps.spec_customization.urls')),
]
