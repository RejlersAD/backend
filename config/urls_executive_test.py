from django.urls import path
from apps.dashboard.executive_views import (
    ExecutiveCustomerInvoiceRegisterView,
    ExecutiveDashboardView,
    ExecutiveReceivablesDashboardView,
)
from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [
    path('api/v1/dashboard/executive/', ExecutiveDashboardView.as_view()),
    path('api/v1/dashboard/executive/receivables/', ExecutiveReceivablesDashboardView.as_view()),
    path('api/v1/dashboard/executive/customer-invoices/', ExecutiveCustomerInvoiceRegisterView.as_view()),
]
secure_module_endpoints(urlpatterns)
