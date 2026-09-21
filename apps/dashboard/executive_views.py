"""Dedicated read grant for the executive operational dashboard."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.finance.command_center_views import (
    CustomerInvoiceRegisterFilters,
    ReceivablesDashboardFilters,
)
from apps.finance.services.customer_invoice_register import build_customer_invoice_register
from apps.finance.services.receivables_dashboard import build_receivables_dashboard
from apps.rbac.permissions import HasModuleAccess
from .executive import build_executive_dashboard


class ExecutiveDashboardView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'executive_dashboard'
    permission_action = 'read'
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        response = Response(build_executive_dashboard(request.user))
        response['Cache-Control'] = 'private, no-store'
        return response


class ExecutiveReceivablesDashboardView(ExecutiveDashboardView):
    """Executive access with the finance builder's independent source grants."""

    def get(self, request):
        filters = ReceivablesDashboardFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        response = Response(build_receivables_dashboard(request.user, **filters.validated_data))
        response['Cache-Control'] = 'private, no-store'
        return response


class ExecutiveCustomerInvoiceRegisterView(ExecutiveDashboardView):
    """Current customer invoices under the executive and outgoing read grants."""

    def get(self, request):
        filters = CustomerInvoiceRegisterFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        response = Response(build_customer_invoice_register(request.user, **filters.validated_data))
        response['Cache-Control'] = 'private, no-store'
        return response
