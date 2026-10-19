"""Explicit Finance refresh command; ordinary report GETs never contact Graph."""
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.permissions import HasModuleAccess
from .services.receivables_sharepoint import sync_enabled, sync_finance_sharepoint
from .services.sharepoint_status import finance_sharepoint_status


def _private_response(data, *, status=200):
    response = Response(data, status=status)
    response['Cache-Control'] = 'private, no-store'
    return response


class FinanceReceivablesRefreshView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance_overview'
    permission_action = 'read'
    http_method_names = ['get', 'post', 'head', 'options']

    def get(self, request):
        return _private_response(finance_sharepoint_status(request.user))

    def post(self, request):
        if not all(module_action_allowed(request.user, 'finance_outgoing', action)
                   for action in ('read', 'update')):
            raise PermissionDenied('Outgoing Invoices read and update access are required to refresh SharePoint data.')
        # This command uses only the server's registered workbook. It cannot
        # accept a different URL, application identity or financial source.
        if not isinstance(request.data, dict) or request.data:
            raise ValidationError('Refresh uses the configured Finance workbook and accepts no request fields.')
        if not sync_enabled():
            return _private_response({
                'detail': 'Finance SharePoint synchronization is disabled.',
                'sync': finance_sharepoint_status(request.user),
            }, status=503)
        try:
            result = sync_finance_sharepoint(scheduled=False)
        except Exception:
            # The domain adapter has already retained its sanitized failure
            # checkpoint. Raw transport/configuration errors never enter HTTP.
            return _private_response({
                'detail': 'The SharePoint refresh failed. The previous validated Finance data remains available.',
                'sync': finance_sharepoint_status(request.user),
            }, status=502)
        return _private_response({
            'status': result['status'], 'sync': finance_sharepoint_status(request.user),
        })
