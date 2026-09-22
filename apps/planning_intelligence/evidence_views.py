"""Authorized evidence review commands, deliberately separate from scheduling."""
import logging

from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects, can_write_project
from .evidence_models import EvidenceGraph
from .models import PlanningProject
from .serializers import PlanningJobSerializer
from .services.audit import record_event
from .services.evidence_graph import (
    EvidenceError, accepted_document_plan, evidence_review, input_fingerprint, materialize_accepted_plan, record_evidence_decision, refresh_evidence_graph,
)
from .services.operational_jobs import canonical_fingerprint, dispatch_job, get_or_create_job


logger = logging.getLogger(__name__)


class BulkEvidenceSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    mode = serializers.ChoiceField(choices=['verified', 'ai_verified'])
    reason = serializers.CharField(max_length=4000, allow_blank=False, trim_whitespace=True)


def _dispatch_bulk_job(job):
    # Dispatch after the HTTP transaction commits. A queue failure is durable
    # on the job and surfaced by polling, without losing the caller's job ID.
    try:
        dispatch_job(job)
    except RuntimeError:
        logger.exception('Could not dispatch evidence review job %s', job.pk)


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
        group = request.query_params.get('group', '')
        if len(group) > 180:
            raise serializers.ValidationError('Select a valid evidence group.')
        data = evidence_review(project, offset=offset, limit=limit, fact_id=request.query_params.get('fact_id'),
                               group=group, include_bulk=True)
        data['master_revision'] = project.master_schedule_revision
        latest_run = project.intelligence_runs.filter(is_deleted=False).order_by('-pk').first()
        data['extraction'] = ({'run_id': latest_run.pk, 'status': latest_run.status,
                              **((latest_run.summary or {}).get('extraction_summary') or {})} if latest_run else None)
        can_review = module_action_allowed(request.user, 'planning_package', 'update') and can_write_project(request.user, project)
        data['permissions'] = {'can_review': can_review, 'can_supply_inputs': can_review, 'can_link': can_review}
        data['capabilities'] = {'correct': True, 'link': True, 'source_preview': True}
        bulk = data.get('bulk_review') or {}
        bulk['enabled'] = bool(bulk.get('enabled') and can_review and not data.get('readiness', {}).get('stale'))
        jobs = project.jobs.filter(is_deleted=False, job_type='evidence_bulk').order_by('-created_at', '-pk')
        active = jobs.filter(status__in=['queued', 'running']).first()
        latest = jobs.first()
        bulk['active_job'] = PlanningJobSerializer(active, context={'request': request}).data if active else None
        matches_revision = latest and str((latest.result_data or {}).get('graph_id')) == str(data.get('graph_id')) and (
            (latest.result_data or {}).get('result_revision') == data.get('revision'))
        failed_current = latest and latest.status in {'failed', 'cancelled'} and (
            (latest.request_data or {}).get('graph_id') == data.get('graph_id') and
            (latest.request_data or {}).get('revision') == data.get('revision'))
        bulk['latest_job'] = PlanningJobSerializer(latest, context={'request': request}).data if latest and (matches_revision or failed_current) else None
        data['bulk_review'] = bulk
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
            if self.operation == 'bulk':
                return self.enqueue_bulk(request, project)
            elif self.operation == 'materialize':
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

    def enqueue_bulk(self, request, project):
        serializer = BulkEvidenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        with transaction.atomic():
            project = PlanningProject.objects.select_for_update().get(pk=project.pk)
            active = project.jobs.filter(is_deleted=False, job_type='evidence_bulk', status__in=['queued', 'running']).first()
            if active:
                return Response(PlanningJobSerializer(active, context={'request': request}).data, status=202)
            graph = EvidenceGraph.objects.select_for_update().filter(project=project).first()
            if not graph or graph.revision != payload['revision']:
                raise EvidenceError('Evidence changed. Reload review before starting bulk acceptance.', 'evidence_revision_conflict')
            if graph.source_fingerprint != input_fingerprint(project):
                raise EvidenceError('Sources changed. Refresh evidence before starting bulk acceptance.', 'evidence_sources_changed')
            if payload['mode'] == 'ai_verified':
                from .services.evidence_bulk_ai import ai_availability
                availability = ai_availability(project)
                if not availability.get('available'):
                    raise EvidenceError(availability.get('reason') or 'Project AI is not configured.', 'evidence_ai_unavailable', 400)
            payload.update(graph_id=str(graph.pk), source_fingerprint=graph.source_fingerprint)
            key = canonical_fingerprint({'operation': 'evidence-bulk-v1', 'project_id': project.pk,
                                         'actor_id': request.user.pk, 'request': payload})
            job, created = get_or_create_job(project, 'evidence_bulk', payload, request.user, idempotency_key=key)
            if created or job.status in {'failed', 'cancelled'}:
                if not created:
                    job.status = 'queued'
                    job.progress = 0
                    job.message = 'Queued for bulk evidence review'
                    job.error_code = ''
                    job.error_message = ''
                    job.result_data = {}
                    job.progress_log = []
                    job.started_at = None
                    job.heartbeat_at = None
                    job.finished_at = None
                    job.save(update_fields=['status', 'progress', 'message', 'error_code', 'error_message',
                                            'result_data', 'progress_log', 'started_at', 'heartbeat_at',
                                            'finished_at', 'updated_at'])
                record_event(project=project, actor=request.user, action='evidence.bulk_queued', entity=job,
                             after={'mode': payload['mode'], 'revision': payload['revision']})
                transaction.on_commit(lambda: _dispatch_bulk_job(job))
        return Response(PlanningJobSerializer(job, context={'request': request}).data, status=202)
