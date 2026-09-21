"""Finance overview read endpoint with independently authorized invoice sources."""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasModuleAccess
from .services.command_center import build_command_center
from .services.receivables_dashboard import build_receivables_dashboard
from .services.customer_invoice_register import ORDERINGS, build_customer_invoice_register


class FinanceCommandCenterView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance_overview'
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        response = Response(build_command_center(request.user))
        response['Cache-Control'] = 'private, no-store'
        return response


class ReceivablesDashboardFilters(serializers.Serializer):
    currency = serializers.RegexField(r'^(?:[A-Za-z]{3,4}|UNSPECIFIED)$', default='AED')
    company = serializers.CharField(required=False, allow_blank=True, default='', max_length=256)
    months = serializers.ChoiceField(choices=[6, 12, 24], default=12)
    as_of = serializers.DateField(required=False)

    def validate_currency(self, value):
        return value.upper()

    def validate_as_of(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('The ageing reference date cannot be in the future.')
        if value.year < 1902:
            raise serializers.ValidationError('The ageing reference date must be in 1902 or later.')
        return value


class FinanceReceivablesDashboardView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance_overview'
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        filters = ReceivablesDashboardFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        response = Response(build_receivables_dashboard(request.user, **filters.validated_data))
        response['Cache-Control'] = 'private, no-store'
        return response


class CustomerInvoiceRegisterFilters(serializers.Serializer):
    currency = serializers.RegexField(r'^(?:[A-Za-z]{3,4}|UNSPECIFIED)$', default='AED')
    company = serializers.CharField(required=False, allow_blank=True, default='', max_length=256)
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.ChoiceField(choices=[8, 20, 50], default=8)
    ordering = serializers.ChoiceField(choices=ORDERINGS, default='-invoice_date')
    as_of = serializers.DateField(required=False)
    validate_as_of = ReceivablesDashboardFilters.validate_as_of

    def validate_currency(self, value):
        return value.upper()


class FinanceCustomerInvoiceRegisterView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance_overview'
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        filters = CustomerInvoiceRegisterFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        response = Response(build_customer_invoice_register(request.user, **filters.validated_data))
        response['Cache-Control'] = 'private, no-store'
        return response
