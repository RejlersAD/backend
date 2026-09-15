"""Finance overview read endpoint with independently authorized invoice sources."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasModuleAccess
from .services.command_center import build_command_center


class FinanceCommandCenterView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance_overview'
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        response = Response(build_command_center(request.user))
        response['Cache-Control'] = 'private, no-store'
        return response
