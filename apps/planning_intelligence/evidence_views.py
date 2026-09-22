"""Authorized evidence review commands, deliberately separate from scheduling."""
from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects, can_write_project
from .services.evidence_graph import (
    EvidenceError, accepted_document_plan, evidence_review, materialize_accepted_plan, record_evidence_decision, refresh_evidence_graph,
)


class EvidenceDecisionSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    action = serializers.ChoiceField(choices=['accept', 'reject', 'correct', 'link'])
    fact_id = serializers.UUIDField()
    target_fact_id = serializers.UUIDField(required=False)
    issue_id = serializers.CharField(required=False, max_length=200)
    value = serializers.JSONField(required=False, allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True, max_length=40)
    reason = serializers.CharField(max_length=4000, allow_blank=False, trim_whitespace=True)

    def validate(self, data):
        if data['action'] == 'correct' and 'value' not in data:
            raise serializers.ValidationError({'value': 'Supply the approved replacement value.'})
        if data['action'] == 'link' and not data.get('target_fact_id'):
            raise serializers.ValidationError({'target_fact_id': 'Select the exact identity to link.'})
        return data


class EvidenceReviewView(APIView):
    permission_classes = [IsAuthenticated]
    operation = None

    def project(self, request, project_id):
        action = 'read' if request.method in {'GET', 'HEAD', 'OPTIONS'} else 'update'
        if not module_action_allowed(request.user, 'planning_package', action):
            raise PermissionDenied('Your access does not permit this evidence review action.')
        project = get_object_or_404(accessible_projects(request.user), pk=project_id)
        if action != 'read' and not can_write_project(request.user, project):
            raise PermissionDenied('Your project role permits viewing, but not changing evidence or planning inputs.')
        return project

    def envelope(self, request, project):
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
            limit = max(1, min(200, int(request.query_params.get('limit', 100))))
        except ValueError:
            raise serializers.ValidationError('Select valid evidence pagination values.')
        data = evidence_review(project, offset=offset, limit=limit, fact_id=request.query_params.get('fact_id'))
        data['master_revision'] = project.master_schedule_revision
        latest_run = project.intelligence_runs.filter(is_deleted=False).order_by('-pk').first()
        data['extraction'] = ({'run_id': latest_run.pk, 'status': latest_run.status,
                              **((latest_run.summary or {}).get('extraction_summary') or {})} if latest_run else None)
        can_review = module_action_allowed(request.user, 'planning_package', 'update') and can_write_project(request.user, project)
        data['permissions'] = {'can_review': can_review, 'can_supply_inputs': can_review, 'can_link': can_review}
        data['capabilities'] = {'correct': True, 'link': True, 'source_preview': True}
        files = {file.pk: file for file in project.files.filter(is_deleted=False)}
        for fact in data['facts']:
            for source in fact['sources']:
                file = files.get(source.get('file_id'))
                try:
                    source['preview_url'] = file.file.url if file and file.file else None
                except (OSError, ValueError):
                    source['preview_url'] = None
        return data

    def get(self, request, project_id):
        project = self.project(request, project_id)
        if self.operation == 'accepted-plan':
            return Response(accepted_document_plan(project))
        if self.operation:
            raise MethodNotAllowed('GET')
        return Response(self.envelope(request, project))

    def post(self, request, project_id):
        project = self.project(request, project_id)
        try:
            if self.operation == 'materialize':
                revision = serializers.IntegerField(min_value=0).run_validation(request.data.get('revision'))
                activate = serializers.BooleanField().run_validation(request.data.get('activate', False))
                master_revision = serializers.IntegerField(min_value=0).run_validation(request.data.get('master_revision')) if activate else None
                from .services.master_schedule import select_master_version
                from .services.schedule_approval import ScheduleApprovalError
                with transaction.atomic():
                    result = materialize_accepted_plan(project, request.user, revision=revision)
                    if activate:
                        try:
                            current = select_master_version(project, request.user, revision=master_revision, version_id=result['schedule_version_id'])
                        except ScheduleApprovalError as exc:
                            raise EvidenceError(str(exc), exc.payload['code'], exc.status_code)
                        result.update(master_revision=current['master_revision'], activated=True)
                return Response(result)
            elif self.operation == 'refresh':
                refresh_evidence_graph(project, request.user)
            elif self.operation == 'decisions':
                serializer = EvidenceDecisionSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                record_evidence_decision(project, request.user, serializer.validated_data)
            else:
                raise MethodNotAllowed('POST')
        except EvidenceError as exc:
            return Response(exc.payload, status=exc.status_code)
        project.refresh_from_db()
        return Response(self.envelope(request, project))
