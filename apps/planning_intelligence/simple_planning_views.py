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
from .services.master_schedule import master_plan_state, master_schedule_action, select_master_version
from .services.simple_planning import (
    analyse_plan, apply_schedule_proposal, approve_publish_plan, plan_state, propose_schedule,
    reopen_plan, save_plan, submit_plan,
)
from .work_breakdown_serializers import ManualWorkBreakdownSaveSerializer
from .services.programmatic_requirements import create_programmatic_draft


class SimplePlanSaveSerializer(ManualWorkBreakdownSaveSerializer):
    advance = None


class SimplePlanActionSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    rebuild = serializers.BooleanField(default=False)
    approver_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    name = serializers.CharField(max_length=255, allow_blank=True, default='')
    proposal_token = serializers.CharField(max_length=4096, required=False)
    workflow_mode = serializers.ChoiceField(choices=['source_only', 'standard_five', 'enterprise'], required=False)
    version_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    requirement_scope = serializers.ChoiceField(choices=['all'], default='all')


class SourceSchedulePreviewSerializer(serializers.Serializer):
    source_file_id = serializers.IntegerField(min_value=1, required=False)
    offset = serializers.IntegerField(min_value=0, max_value=1000000, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=100)
    search = serializers.CharField(max_length=250, allow_blank=True, default='')


class ParallelLogicReviewSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    fingerprint = serializers.RegexField(r'^[a-f0-9]{64}$')
    group_id = serializers.RegexField(r'^[a-f0-9]{64}$')
    rationale = serializers.CharField(min_length=20, max_length=5000)
    capacity_basis = serializers.CharField(min_length=20, max_length=5000)
    duration_basis = serializers.CharField(min_length=20, max_length=5000)
    max_parallel_deliverables = serializers.IntegerField(min_value=1, max_value=100000)


class SourceScheduleImportPreviewSerializer(serializers.Serializer):
    source_file_id = serializers.IntegerField(min_value=1)
    master_revision = serializers.IntegerField(min_value=0)


class SourceScheduleImportApplySerializer(serializers.Serializer):
    proposal_token = serializers.CharField(max_length=4096)
    reason = serializers.CharField(max_length=2000, allow_blank=False)
    acknowledge_scope = serializers.BooleanField()


class IntelligentSequencePreviewSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)


class SourceLogicPreviewSerializer(serializers.Serializer):
    source_version_id = serializers.IntegerField(min_value=1)
    revision = serializers.IntegerField(min_value=0)
    calendar_spec = serializers.JSONField()
    reason = serializers.CharField(max_length=2000, allow_blank=False)


class SourceLogicApplySerializer(serializers.Serializer):
    preview_token = serializers.CharField(max_length=8192)
    reason = serializers.CharField(max_length=2000, allow_blank=False)


class IntelligentSequenceApplySerializer(serializers.Serializer):
    proposal_token = serializers.CharField(max_length=4096)


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
        if self.operation == 'source-preview':
            from .services.source_schedule_preview import source_schedule_preview
            project = self.project(request, project_id)
            serializer = SourceSchedulePreviewSerializer(data=request.query_params)
            serializer.is_valid(raise_exception=True)
            return Response(source_schedule_preview(project, actor=request.user, **serializer.validated_data))
        if self.operation:
            raise MethodNotAllowed('GET')
        version = request.query_params.get('version_id')
        if version is not None and (not version.isdigit() or len(version) > 18 or int(version) < 1):
            raise serializers.ValidationError({'version_id': 'Select a valid schedule version.'})
        return Response(master_plan_state(self.project(request, project_id), request.user, version_id=int(version) if version else None))

    def put(self, request, project_id):
        if self.operation:
            raise MethodNotAllowed('PUT')
        if request.query_params.get('version_id') or request.data.get('viewing_history'):
            return Response({'error': 'Return to the current draft before editing activities.',
                             'code': 'simple_plan_history_read_only'}, status=409)
        project = self.project(request, project_id)
        if project.master_schedule_version_id:
            return Response({'error': 'Edit accepted inputs in Evidence, or open the preserved working draft.',
                             'code': 'master_schedule_accepted_inputs_read_only'}, status=409)
        serializer = SimplePlanSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.perform(lambda: save_plan(project, request.user, serializer.validated_data))

    patch = put

    def post(self, request, project_id):
        if self.operation in {'edit-activity', 'edit-row'}:
            from .gantt_serializers import GanttEditSerializer, GanttRowEditSerializer
            from .services.gantt_editing import edit_gantt
            from .services.gantt_rows import edit_gantt_row
            project = self.project(request, project_id)
            if request.query_params.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current draft before editing activities.',
                                 'code': 'simple_plan_history_read_only'}, status=409)
            serializer = (GanttRowEditSerializer if self.operation == 'edit-row' else GanttEditSerializer)(data=request.data)
            serializer.is_valid(raise_exception=True)
            operation = edit_gantt_row if self.operation == 'edit-row' else edit_gantt
            return self.perform(lambda: operation(project, request.user, serializer.validated_data))
        if not self.operation or self.operation == 'source-preview':
            raise MethodNotAllowed('POST')
        project = self.project(request, project_id)
        if self.operation == 'confirm-parallel-logic':
            from .services.schedule_logic_review import confirm_parallel_logic
            if request.query_params.get('version_id') or request.data.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current editable schedule before reviewing parallel work.',
                                 'code': 'simple_plan_history_read_only'}, status=409)
            serializer = ParallelLogicReviewSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            return self.perform(lambda: confirm_parallel_logic(project, request.user, serializer.validated_data))
        if self.operation in {'preview-source-logic', 'apply-source-logic'}:
            from .services.source_schedule_logic import preview_source_logic, apply_source_logic
            if request.query_params.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current source schedule before building a planning revision.',
                                 'code': 'simple_plan_history_read_only'}, status=409)
            preview = self.operation == 'preview-source-logic'
            serializer = (SourceLogicPreviewSerializer if preview else SourceLogicApplySerializer)(data=request.data)
            serializer.is_valid(raise_exception=True)
            operation = preview_source_logic if preview else apply_source_logic
            return self.perform(lambda: operation(project, request.user, **serializer.validated_data))
        if self.operation in {'propose-intelligent-sequence', 'apply-intelligent-sequence'}:
            from .services.intelligent_sequence import propose_intelligent_sequence, apply_intelligent_sequence
            if request.query_params.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current draft before generating a sequence.',
                                 'code': 'simple_plan_history_read_only'}, status=409)
            preview = self.operation == 'propose-intelligent-sequence'
            serializer = (IntelligentSequencePreviewSerializer if preview else IntelligentSequenceApplySerializer)(data=request.data)
            serializer.is_valid(raise_exception=True)
            operation = propose_intelligent_sequence if preview else apply_intelligent_sequence
            return self.perform(lambda: operation(project, request.user, **serializer.validated_data))
        if self.operation in {'preview-source-import', 'apply-source-import'}:
            from .services.source_schedule_import import preview_source_import, apply_source_import
            if request.query_params.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current schedule before importing a source.',
                                 'code': 'simple_plan_history_read_only'}, status=409)
            preview = self.operation == 'preview-source-import'
            serializer = (SourceScheduleImportPreviewSerializer if preview else SourceScheduleImportApplySerializer)(data=request.data)
            serializer.is_valid(raise_exception=True)
            operation = preview_source_import if preview else apply_source_import
            return self.perform(lambda: operation(project, request.user, **serializer.validated_data))
        serializer = SimplePlanActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if self.operation == 'programmatic-draft':
            if request.query_params.get('version_id') or request.data.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current editable draft before creating activities.',
                                 'code': 'simple_plan_history_read_only'}, status=409)
            return self.perform(lambda: create_programmatic_draft(project, request.user,
                revision=data['revision'], requirement_scope=data['requirement_scope']))
        if self.operation == 'select-version':
            if 'version_id' not in data:
                raise serializers.ValidationError({'version_id': 'Select a schedule version or null for the working draft.'})
            return self.perform(lambda: select_master_version(project, request.user, revision=data['revision'], version_id=data['version_id']))
        if project.master_schedule_version_id:
            if request.query_params.get('version_id') or request.data.get('viewing_history'):
                return Response({'error': 'Return to the current schedule before making changes.', 'code': 'simple_plan_history_read_only'}, status=409)
            return self.perform(lambda: master_schedule_action(project, request.user, operation=self.operation,
                revision=data['revision'], approver_id=data.get('approver_id'), name=data['name']))
        if self.operation in {'calculate', 'validate'}:
            return Response({'error': 'Select an accepted schedule version before this operation.', 'code': 'master_schedule_not_selected'}, status=409)
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

    def perform(self, operation):
        try:
            result = operation()
            plan = result.get('plan', result) if isinstance(result, dict) else None
            if isinstance(plan, dict) and 'tasks' in plan and 'canonical_version' not in plan:
                from .models import PlanningProject
                from .services.planning_profiles import planning_profile_selection
                from .services.planning_provenance import annotate_plan_provenance
                project = PlanningProject.objects.get(pk=plan['project_id'], is_deleted=False)
                annotate_plan_provenance(project, plan)
                plan.update(canonical_version=False, master_revision=project.master_schedule_revision,
                            master_version_id=project.master_schedule_version_id,
                            planning_profile=planning_profile_selection(project))
            return Response(result)
        except ScheduleApprovalError as exc:
            return Response(exc.payload, status=exc.status_code)
        except SchedulingError as exc:
            return Response({'error': str(exc), 'code': exc.code, 'blockers': exc.issues}, status=409)
        except ValueError as exc:
            return Response({'error': str(exc), 'code': 'simple_plan_validation'}, status=409)
