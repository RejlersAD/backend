"""Dedicated read grant for the executive operational dashboard."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
