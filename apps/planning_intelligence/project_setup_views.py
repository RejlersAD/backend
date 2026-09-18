"""Preview an AI/template plan, then create it in one explicit action."""
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle

from .project_setup_serializers import PROJECT_TYPES, ProjectSetupAISettingsSerializer, ProjectSetupCreateSerializer
from .services.project_setup import build_preview, create_from_preview, employee_payload, require_setup_access, setup_employees
from .services.project_setup_ai import ai_settings_payload, delete_settings, test_and_save_settings


class SetupPreviewThrottle(UserRateThrottle):
    rate = '12/hour'
    scope = 'project_setup_preview'


class SetupAISettingsThrottle(UserRateThrottle):
    rate = '10/hour'
    scope = 'project_setup_ai_settings'


@method_decorator(sensitive_post_parameters('api_key'), name='dispatch')
class ProjectSetupAISettingsView(APIView):
    permission_classes = [IsAuthenticated]

    @property
    def permission_action(self):
        # Removing a personal credential edits connection settings; it does not
        # delete a planning project. The setup access checks still apply below.
        return 'read' if self.request.method in {'GET', 'HEAD', 'OPTIONS'} else 'update'

    def get_throttles(self):
        return [SetupAISettingsThrottle()] if self.request.method == 'POST' else []

    def get(self, request):
        require_setup_access(request.user)
        return Response(ai_settings_payload(request.user))

    @sensitive_variables()
    def post(self, request):
        require_setup_access(request.user)
        serializer = ProjectSetupAISettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(test_and_save_settings(request.user, serializer.validated_data))

    def delete(self, request):
        require_setup_access(request.user)
        return Response(delete_settings(request.user))


class ProjectSetupOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        require_setup_access(request.user)
        return Response({'project_types': [{'value': key, 'label': label} for key, label in PROJECT_TYPES],
                         'employees': [employee_payload(row) for row in setup_employees(request.user)],
                         **ai_settings_payload(request.user)})


class ProjectSetupPreviewView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SetupPreviewThrottle]

    def post(self, request):
        return Response(build_preview(request.data, request.user))


class ProjectSetupCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ProjectSetupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = create_from_preview(data['preview_token'], data.get('plan'), request.user)
        return Response(result, status=200 if result['repeated'] else 201)
