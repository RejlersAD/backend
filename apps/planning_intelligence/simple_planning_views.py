"""Project-scoped endpoints for the single planning canvas."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects
from .services.cpm import SchedulingError
from .services.schedule_approval import ScheduleApprovalError
from .services.simple_planning import (
    analyse_plan, apply_schedule_proposal, approve_publish_plan, plan_state, propose_schedule,
    reopen_plan, save_plan, submit_plan,
)
from .work_breakdown_serializers import ManualWorkBreakdownSaveSerializer


class SimplePlanSaveSerializer(ManualWorkBreakdownSaveSerializer):
    advance = None


class SimplePlanActionSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    rebuild = serializers.BooleanField(default=False)
    approver_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    name = serializers.CharField(max_length=255, allow_blank=True, default='')
    proposal_token = serializers.CharField(max_length=4096, required=False)
    workflow_mode = serializers.ChoiceField(choices=['standard_five'], required=False)


class SimplePlanningView(APIView):
    permission_classes = [IsAuthenticated]
    # The publish command delegates to the current assigned-review, assurance
    # and project-authority services before creating its immutable snapshot.
    business_approval_actions = {'SimplePlanningView'}
    operation = None

    @property
    def permission_action(self):
        if self.request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return 'read'
        return 'approve' if self.operation == 'approve-publish' else 'update'

    def project(self, request, project_id):
        if not module_action_allowed(request.user, 'planning_package', self.permission_action):
            raise PermissionDenied('Your access does not permit this planning action.')
        return get_object_or_404(accessible_projects(request.user), pk=project_id)

    def get(self, request, project_id):
        if self.operation:
            raise MethodNotAllowed('GET')
        version = request.query_params.get('version_id')
        if version is not None and (not version.isdigit() or len(version) > 18 or int(version) < 1):
            raise serializers.ValidationError({'version_id': 'Select a valid schedule version.'})
        return Response(plan_state(self.project(request, project_id), request.user, version_id=int(version) if version else None))

    def put(self, request, project_id):
        if self.operation:
            raise MethodNotAllowed('PUT')
        project = self.project(request, project_id)
        serializer = SimplePlanSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.perform(lambda: save_plan(project, request.user, serializer.validated_data))

    patch = put

    def post(self, request, project_id):
        if not self.operation:
            raise MethodNotAllowed('POST')
        project = self.project(request, project_id)
        serializer = SimplePlanActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if self.operation in {'propose-schedule', 'apply-schedule'}:
            if request.query_params.get('version_id') or request.data.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current editable draft before building a schedule.', 'code': 'simple_plan_history_read_only'}, status=409)
            if self.operation == 'apply-schedule' and not data.get('proposal_token'):
                raise serializers.ValidationError({'proposal_token': 'Build and review a schedule proposal first.'})
        operation = {
            'analyse': lambda: analyse_plan(project, request.user, revision=data['revision'], rebuild=data['rebuild']),
            'submit': lambda: submit_plan(project, request.user, revision=data['revision'], approver_id=data.get('approver_id')),
            'approve-publish': lambda: approve_publish_plan(project, request.user, revision=data['revision'], name=data['name']),
            'reopen': lambda: reopen_plan(project, request.user, revision=data['revision']),
            'propose-schedule': lambda: propose_schedule(project, request.user, revision=data['revision'], workflow_mode=data.get('workflow_mode')),
            'apply-schedule': lambda: apply_schedule_proposal(project, request.user, revision=data['revision'], proposal_token=data['proposal_token']),
        }[self.operation]
        return self.perform(operation)

    @staticmethod
    def perform(operation):
        try:
            return Response(operation())
        except ScheduleApprovalError as exc:
            return Response(exc.payload, status=exc.status_code)
        except SchedulingError as exc:
            return Response({'error': str(exc), 'code': exc.code, 'blockers': exc.issues}, status=409)
        except ValueError as exc:
            return Response({'error': str(exc), 'code': 'simple_plan_validation'}, status=409)
