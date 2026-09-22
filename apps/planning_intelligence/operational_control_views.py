from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as ModelValidationError
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects
from .operational_control_serializers import OperationalCommandSerializer
from .services.operational_controls import operational_control_command, operational_control_state
from .services.schedule_approval import ScheduleApprovalError


class OperationalControlsView(APIView):
    permission_classes = [IsAuthenticated]
    business_approval_actions = {'OperationalControlsView'}

    @property
    def permission_action(self):
        request = getattr(self, 'request', None)
        if not request or request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return 'read'
        command = request.data.get('action') if isinstance(request.data, dict) else None
        if command in {'approve_policy', 'publish_report'}:
            return 'approve'
        if command == 'return_report' and not module_action_allowed(request.user, 'planning_package', 'update'):
            return 'approve'
        return 'update'

    def get(self, request, project_id):
        project = get_object_or_404(accessible_projects(request.user), pk=project_id)
        identifiers = {key: serializers.IntegerField(min_value=1).run_validation(request.query_params[key])
                       for key in ('baseline_id', 'report_id') if key in request.query_params}
        return Response(operational_control_state(project, request.user, **identifiers))

    def post(self, request, project_id):
        project = get_object_or_404(accessible_projects(request.user), pk=project_id)
        serializer = OperationalCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(operational_control_command(project, request.user, serializer.validated_data))
        except ScheduleApprovalError as exc:
            return Response(exc.payload, status=exc.status_code)
        except ModelValidationError as exc:
            raise serializers.ValidationError(getattr(exc, 'message_dict', exc.messages)) from exc
