from django.urls import path
from .views import (
    PersonalDashboardView,
    PersonalInsightsView,
    ProjectControlBundleView,
)
from apps.api.views import aws_status, aws_report
from .executive_views import (
    ExecutiveCustomerInvoiceRegisterView,
    ExecutiveDashboardView,
    ExecutiveReceivablesDashboardView,
)
from .work_hub import WorkHubView
from .work_hub_tasks import WorkHubTaskView

urlpatterns = [
    path('work-hub/', WorkHubView.as_view(), name='work-hub'),
    path('work-hub/tasks/<int:task_id>/', WorkHubTaskView.as_view(), name='work-hub-task'),
    path('executive/', ExecutiveDashboardView.as_view(), name='executive-dashboard'),
    path('executive/receivables/', ExecutiveReceivablesDashboardView.as_view(), name='executive-receivables'),
    path('executive/customer-invoices/', ExecutiveCustomerInvoiceRegisterView.as_view(), name='executive-customer-invoices'),
    path('personal/', PersonalDashboardView.as_view(), name='personal-dashboard'),
    path('personal/insights/', PersonalInsightsView.as_view(), name='personal-insights'),
    path('personal/project-control/', ProjectControlBundleView.as_view(), name='personal-project-control'),
    path('aws-status/', aws_status, name='dashboard-aws-status'),
    path('aws-report/', aws_report, name='dashboard-aws-report'),
]
