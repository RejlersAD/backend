from django.urls import path
from .views import (
    PersonalDashboardView,
    PersonalInsightsView,
    ProjectControlBundleView,
)
from apps.api.views import aws_status, aws_report

urlpatterns = [
    path('personal/', PersonalDashboardView.as_view(), name='personal-dashboard'),
    path('personal/insights/', PersonalInsightsView.as_view(), name='personal-insights'),
    path('personal/project-control/', ProjectControlBundleView.as_view(), name='personal-project-control'),
    path('aws-status/', aws_status, name='dashboard-aws-status'),
    path('aws-report/', aws_report, name='dashboard-aws-report'),
]
