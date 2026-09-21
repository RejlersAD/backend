"""Explicit preview, application and management endpoints for planning builds."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects, can_write_project
from .models import PlanningBuild, PlanningProject, ScheduleVersion
from .services.audit import record_event
from .services.planning_builds import (preview_planning_build, apply_planning_build, planning_build_collection,
                                      serialize_planning_build)
from .services.planning_registers import manage_risk, risk_collection, seed_build_risks
from .services.schedule_approval import current_schedule_version


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: 'This field is not supported by this command.' for key in unknown})
        return super().to_internal_value(data)


class BuildPreviewSerializer(StrictSerializer):
    evidence_revision = serializers.IntegerField(min_value=1)
    profile_selection_revision = serializers.IntegerField(min_value=1)
    options = serializers.JSONField(default=dict)
    reason = serializers.CharField(max_length=4000)


class BuildApplySerializer(StrictSerializer):
    fingerprint = serializers.CharField(min_length=64, max_length=64)
    master_revision = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(max_length=4000)


class ProjectPlanningView(APIView):
    permission_classes = [IsAuthenticated]
    operation = None

    @property
    def permission_action(self):
        return 'read' if self.request.method in {'GET', 'HEAD', 'OPTIONS'} else 'update'

    def project(self, request, project_id):
        if not module_action_allowed(request.user, 'planning_package', self.permission_action):
            raise PermissionDenied('Your access does not permit this planning action.')
        return get_object_or_404(accessible_projects(request.user), pk=project_id)


class PlanningBuildView(ProjectPlanningView):
    def collection(self, project, actor):
        editable = can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update')
        return {**planning_build_collection(project, actor), 'master_revision': project.master_schedule_revision,
                'permissions': {'can_preview': editable, 'can_apply': editable}}

    def get(self, request, project_id, build_id=None):
        if self.operation:
            raise MethodNotAllowed('GET')
        project = self.project(request, project_id)
        if build_id is None:
            return Response(self.collection(project, request.user))
        build = get_object_or_404(PlanningBuild, project=project, pk=build_id)
        return Response(serialize_planning_build(build))

    def post(self, request, project_id, build_id=None):
        project = self.project(request, project_id)
        if self.operation is None and build_id is None:
            serializer = BuildPreviewSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            build = preview_planning_build(project, request.user, **serializer.validated_data)
            return Response(serialize_planning_build(build), status=201)
        if self.operation != 'apply' or build_id is None:
            raise MethodNotAllowed('POST')
        serializer = BuildApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # Application and activation are one transaction. A stale selection
        # cannot leave an unintended version or replace another user's work.
        with transaction.atomic():
            project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
            if project.master_schedule_revision != data['master_revision']:
                return Response({'code': 'master_schedule_revision_conflict',
                                 'error': 'The Master Schedule selection changed. Refresh before applying this build.'}, status=409)
            build = get_object_or_404(PlanningBuild, project=project, pk=build_id)
            version = apply_planning_build(build, request.user, fingerprint=data['fingerprint'], reason=data['reason'])
            if not current_schedule_version(version):
                raise serializers.ValidationError({'build_id': 'This build has a newer schedule revision. Open that revision or prepare a new build.'})
            seed_build_risks(version)
            before = project.master_schedule_version_id
            project.master_schedule_version = version
            project.master_schedule_revision += 1
            project.save(update_fields=['master_schedule_version', 'master_schedule_revision'])
            record_event(project=project, actor=request.user, action='master_schedule.selected', entity=project,
                         before={'version_id': before}, after={'version_id': version.pk, 'revision': project.master_schedule_revision},
                         metadata={'build_id': str(build.pk), 'reason': data['reason']})
        return Response({'version_id': version.pk, 'schedule_version_id': version.pk, 'activated': True,
                         'master_revision': project.master_schedule_revision})


class RiskInputSerializer(StrictSerializer):
    version_id = serializers.IntegerField(min_value=1)
    item_id = serializers.IntegerField(min_value=1, required=False)
    revision = serializers.IntegerField(min_value=1, required=False)
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=20000, required=False)
    status = serializers.ChoiceField(choices=['open', 'monitoring', 'mitigated', 'closed'], required=False)
    priority = serializers.ChoiceField(choices=['low', 'medium', 'high', 'critical'], allow_null=True, required=False)
    owner_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    response = serializers.CharField(max_length=20000, allow_blank=True, required=False)
    resolution = serializers.CharField(max_length=20000, allow_blank=True, required=False)
    reason = serializers.CharField(max_length=4000)


class PlanningRiskView(ProjectPlanningView):
    def get(self, request, project_id):
        project = self.project(request, project_id)
        version_id = request.query_params.get('version_id') or project.master_schedule_version_id
        if not version_id:
            return Response({'items': [], 'owners': [], 'version_id': None, 'revision': None,
                             'permissions': {'can_create': False, 'can_edit': False}})
        field = serializers.IntegerField(min_value=1)
        version_id = field.run_validation(version_id)
        version = get_object_or_404(ScheduleVersion, pk=version_id, schedule__project=project, is_deleted=False, schedule__is_deleted=False)
        return Response(risk_collection(version, request.user))

    def post(self, request, project_id):
        return self.mutate(request, project_id, create=True)

    def patch(self, request, project_id):
        return self.mutate(request, project_id, create=False)

    def mutate(self, request, project_id, *, create):
        project = self.project(request, project_id)
        serializer = RiskInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        required = {'title', 'description'} if create else {'item_id', 'revision'}
        if required - set(data):
            raise serializers.ValidationError({key: 'This field is required.' for key in required - set(data)})
        forbidden = {'item_id', 'revision'} if create else {'title', 'description'}
        if forbidden & set(data):
            raise serializers.ValidationError({key: 'Source identity cannot be changed by this command.' for key in forbidden & set(data)})
        return Response(manage_risk(project, request.user, data, create=create), status=201 if create else 200)
