from django.urls import path
from .views import (
    PersonalDashboardView,
    PersonalInsightsView,
    ProjectControlBundleView,
)
from apps.api.views import aws_status, aws_report
from .executive_views import ExecutiveDashboardView
from .work_hub import WorkHubView

urlpatterns = [
    path('work-hub/', WorkHubView.as_view(), name='work-hub'),
    path('executive/', ExecutiveDashboardView.as_view(), name='executive-dashboard'),
    path('personal/', PersonalDashboardView.as_view(), name='personal-dashboard'),
    path('personal/insights/', PersonalInsightsView.as_view(), name='personal-insights'),
    path('personal/project-control/', ProjectControlBundleView.as_view(), name='personal-project-control'),
    path('aws-status/', aws_status, name='dashboard-aws-status'),
    path('aws-report/', aws_report, name='dashboard-aws-report'),
]
