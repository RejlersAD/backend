"""Secured APIs for Phase A workflow and engineering-logic configuration."""
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import ValidationError

from .access import accessible_projects, can_final_approve_defaults, can_write_project
from .models import (
    EngineeringDependencyRule, EngineeringDependencyTemplate, PlanningProject,
    ProjectScheduleConfiguration, ScheduleDefaultProposal, WorkflowStage, WorkflowTemplate,
    WorkflowTemplateOverride,
)
from .services.audit import record_event
from .services.default_approval import run_default_acceptance_tests
from .workflow_serializers import (
    EngineeringDependencyTemplateSerializer, ProjectScheduleConfigurationSerializer,
    ScheduleDefaultProposalSerializer, WorkflowTemplateOverrideSerializer, WorkflowTemplateSerializer,
)


def _writable_template(user, template):
    if template.is_system or template.project_id is None:
        raise PermissionDenied('System templates are protected. Clone the template before editing it.')
    if not can_write_project(user, template.project):
        raise PermissionDenied('You cannot modify templates for this project.')
    if template.status != 'draft':
        raise ValidationError('Active or retired templates are immutable. Create a new revision instead.')


class WorkflowTemplateViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowTemplateSerializer
    queryset = WorkflowTemplate.objects.filter(is_deleted=False).select_related(
        'project', 'supersedes', 'created_by',
    ).prefetch_related('stages')

    def get_queryset(self):
        projects = accessible_projects(self.request.user)
        queryset = self.queryset.filter(Q(project__isnull=True) | Q(project__in=projects)).distinct()
        project_id = self.request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(Q(project__isnull=True) | Q(project_id=project_id))
        return queryset

    def perform_create(self, serializer):
        project = serializer.validated_data.get('project')
        if not project or not can_write_project(self.request.user, project):
            raise PermissionDenied('A writable project is required for a custom workflow template.')
        template = serializer.save(
            created_by=self.request.user, is_system=False, is_default=False, status='draft',
        )
        record_event(project=project, actor=self.request.user, action='workflow_template.created', entity=template)

    def perform_update(self, serializer):
        _writable_template(self.request.user, serializer.instance)
        before = WorkflowTemplateSerializer(serializer.instance).data
        template = serializer.save()
        record_event(
            project=template.project, actor=self.request.user, action='workflow_template.updated',
            entity=template, before=before, after=WorkflowTemplateSerializer(template).data,
        )

    def perform_destroy(self, instance):
        _writable_template(self.request.user, instance)
        instance.soft_delete()
        record_event(project=instance.project, actor=self.request.user, action='workflow_template.archived', entity=instance)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone(self, request, pk=None):
        source = self.get_object()
        project = PlanningProject.objects.filter(
            pk=request.data.get('project'), is_deleted=False,
        ).first()
        if not project or not accessible_projects(request.user).filter(pk=project.pk).exists():
            raise ValidationError({'project': 'Accessible planning project is required.'})
        if not can_write_project(request.user, project):
            raise PermissionDenied('You cannot create templates for this project.')
        with transaction.atomic():
            PlanningProject.objects.select_for_update().get(pk=project.pk)
            version = (
                WorkflowTemplate.objects.filter(project=project, code=source.code)
                .aggregate(value=Max('version'))['value'] or 0
            ) + 1
            template = WorkflowTemplate.objects.create(
                project=project, code=source.code, name=source.name,
                description=source.description, version=version, status='draft',
                supersedes=source, created_by=request.user,
            )
            WorkflowStage.objects.bulk_create([
                WorkflowStage(
                    template=template, sequence=row.sequence, code=row.code, name=row.name,
                    activity_name_template=row.activity_name_template, duration_days=row.duration_days,
                    responsible_party=row.responsible_party, activity_type=row.activity_type,
                    relationship_to_previous=row.relationship_to_previous, lag_days=row.lag_days,
                    progress_weight=row.progress_weight, is_release_gate=row.is_release_gate,
                )
                for row in source.stages.filter(is_deleted=False)
            ])
        record_event(project=project, actor=request.user, action='workflow_template.cloned', entity=template, after={'source_id': source.id})
        return Response(self.get_serializer(template).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        template = self.get_object()
        _writable_template(request.user, template)
        with transaction.atomic():
            list(WorkflowTemplate.objects.select_for_update().filter(project=template.project, code=template.code))
            WorkflowTemplate.objects.filter(
                project=template.project, code=template.code, status='active', is_deleted=False,
            ).exclude(pk=template.pk).update(status='retired')
            template.status = 'active'
            template.save(update_fields=['status', 'updated_at'])
        record_event(project=template.project, actor=request.user, action='workflow_template.activated', entity=template)
        return Response(self.get_serializer(template).data)


class EngineeringDependencyTemplateViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EngineeringDependencyTemplateSerializer
    queryset = EngineeringDependencyTemplate.objects.filter(is_deleted=False).select_related(
        'project', 'supersedes', 'created_by',
    ).prefetch_related('rules')

    def get_queryset(self):
        projects = accessible_projects(self.request.user)
        queryset = self.queryset.filter(Q(project__isnull=True) | Q(project__in=projects)).distinct()
        project_id = self.request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(Q(project__isnull=True) | Q(project_id=project_id))
        return queryset

    def perform_create(self, serializer):
        project = serializer.validated_data.get('project')
        if not project or not can_write_project(self.request.user, project):
            raise PermissionDenied('A writable project is required for a custom dependency template.')
        template = serializer.save(
            created_by=self.request.user, is_system=False, is_default=False, status='draft',
        )
        record_event(project=project, actor=self.request.user, action='dependency_template.created', entity=template)

    def perform_update(self, serializer):
        _writable_template(self.request.user, serializer.instance)
        before = EngineeringDependencyTemplateSerializer(serializer.instance).data
        template = serializer.save()
        record_event(
            project=template.project, actor=self.request.user, action='dependency_template.updated',
            entity=template, before=before, after=EngineeringDependencyTemplateSerializer(template).data,
        )

    def perform_destroy(self, instance):
        _writable_template(self.request.user, instance)
        instance.soft_delete()
        record_event(project=instance.project, actor=self.request.user, action='dependency_template.archived', entity=instance)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone(self, request, pk=None):
        source = self.get_object()
        project = PlanningProject.objects.filter(pk=request.data.get('project'), is_deleted=False).first()
        if not project or not accessible_projects(request.user).filter(pk=project.pk).exists():
            raise ValidationError({'project': 'Accessible planning project is required.'})
        if not can_write_project(request.user, project):
            raise PermissionDenied('You cannot create templates for this project.')
        with transaction.atomic():
            PlanningProject.objects.select_for_update().get(pk=project.pk)
            version = (
                EngineeringDependencyTemplate.objects.filter(project=project, code=source.code)
                .aggregate(value=Max('version'))['value'] or 0
            ) + 1
            template = EngineeringDependencyTemplate.objects.create(
                project=project, code=source.code, name=source.name, discipline=source.discipline,
                description=source.description, version=version, status='draft',
                supersedes=source, created_by=request.user,
            )
            EngineeringDependencyRule.objects.bulk_create([
                EngineeringDependencyRule(
                    template=template, sequence=row.sequence,
                    predecessor_code=row.predecessor_code, predecessor_name=row.predecessor_name,
                    predecessor_stage_code=row.predecessor_stage_code,
                    successor_code=row.successor_code, successor_name=row.successor_name,
                    successor_stage_code=row.successor_stage_code,
                    relationship_type=row.relationship_type, lag_days=row.lag_days,
                    rationale=row.rationale, source_reference=row.source_reference,
                    requires_confirmation=row.requires_confirmation,
                )
                for row in source.rules.filter(is_deleted=False)
            ])
        record_event(project=project, actor=request.user, action='dependency_template.cloned', entity=template, after={'source_id': source.id})
        return Response(self.get_serializer(template).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        template = self.get_object()
        _writable_template(request.user, template)
        with transaction.atomic():
            list(EngineeringDependencyTemplate.objects.select_for_update().filter(project=template.project, code=template.code))
            EngineeringDependencyTemplate.objects.filter(
                project=template.project, code=template.code, status='active', is_deleted=False,
            ).exclude(pk=template.pk).update(status='retired')
            template.status = 'active'
            template.save(update_fields=['status', 'updated_at'])
        record_event(project=template.project, actor=request.user, action='dependency_template.activated', entity=template)
        return Response(self.get_serializer(template).data)


class ProjectScheduleConfigurationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']
    serializer_class = ProjectScheduleConfigurationSerializer
    queryset = ProjectScheduleConfiguration.objects.filter(is_deleted=False).select_related(
        'project', 'workflow_template', 'dependency_template', 'updated_by',
    ).prefetch_related('overrides__workflow_template')

    def get_queryset(self):
        queryset = self.queryset.filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not can_write_project(self.request.user, project):
            raise PermissionDenied('You cannot configure this planning workspace.')
        configuration = serializer.save(updated_by=self.request.user)
        record_event(project=project, actor=self.request.user, action='schedule_configuration.created', entity=configuration)

    def perform_update(self, serializer):
        configuration = serializer.instance
        if not can_write_project(self.request.user, configuration.project):
            raise PermissionDenied('You cannot configure this planning workspace.')
        raise ValidationError({
            'detail': 'Scheduling defaults are controlled. Submit a default proposal and obtain final approval.',
            'code': 'schedule_default_approval_required',
        })


class ScheduleDefaultProposalViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']
    serializer_class = ScheduleDefaultProposalSerializer
    queryset = ScheduleDefaultProposal.objects.filter(is_deleted=False).select_related(
        'project__enterprise_project', 'configuration', 'proposed_by', 'decided_by',
    )

    def get_queryset(self):
        queryset = self.queryset.filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def create(self, request, *args, **kwargs):
        project = PlanningProject.objects.filter(pk=request.data.get('project'), is_deleted=False).select_related('enterprise_project').first()
        if not project or not accessible_projects(request.user).filter(pk=project.pk).exists():
            raise ValidationError({'project': 'An accessible planning project is required.'})
        if not can_write_project(request.user, project):
            raise PermissionDenied('You cannot propose defaults for this workspace.')
        configuration = ProjectScheduleConfiguration.objects.filter(project=project, is_deleted=False).first()
        if not configuration:
            raise ValidationError({'project': 'Create the initial schedule configuration before proposing a change.'})
        workflow = WorkflowTemplate.objects.filter(pk=request.data.get('workflow_template'), is_deleted=False, status='active').first()
        dependency_id = request.data.get('dependency_template')
        dependency = EngineeringDependencyTemplate.objects.filter(pk=dependency_id, is_deleted=False, status='active').first() if dependency_id else None
        if not workflow or workflow.project_id not in (None, project.id):
            raise ValidationError({'workflow_template': 'Select an active system or project workflow.'})
        if dependency_id and (not dependency or dependency.project_id not in (None, project.id)):
            raise ValidationError({'dependency_template': 'Select an active system or project dependency template.'})
        confirmed = request.data.get('confirmed_dependency_rule_ids', [])
        if not isinstance(confirmed, list) or any(not str(value).isdigit() for value in confirmed):
            raise ValidationError({'confirmed_dependency_rule_ids': 'Provide a list of rule IDs.'})
        valid_rule_ids = set(dependency.rules.filter(is_deleted=False).values_list('id', flat=True)) if dependency else set()
        if {int(value) for value in confirmed} - valid_rule_ids:
            raise ValidationError({'confirmed_dependency_rule_ids': 'A confirmed rule is outside the selected template.'})
        settings = {**(configuration.settings or {}), 'date_authority': 'relational_cpm', 'confirmed_dependency_rule_ids': [int(value) for value in confirmed]}
        proposed_values = {
            'workflow_template': workflow.id,
            'workflow_name': workflow.name,
            'workflow_version': workflow.version,
            'dependency_template': dependency.id if dependency else None,
            'dependency_name': dependency.name if dependency else '',
            'dependency_version': dependency.version if dependency else None,
            'standard_task_count': workflow.stages.filter(is_deleted=False).count(),
            'settings': settings,
        }
        tests = run_default_acceptance_tests(workflow, dependency, settings)
        existing = ScheduleDefaultProposal.objects.filter(
            project=project, configuration=configuration, status='proposed',
            base_configuration_version=configuration.configuration_version,
            proposed_values=proposed_values, is_deleted=False,
        ).order_by('-created_at').first()
        if existing:
            return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        proposal = ScheduleDefaultProposal.objects.create(
            project=project, configuration=configuration,
            title=(request.data.get('title') or 'Scheduling default change').strip(),
            rationale=(request.data.get('rationale') or '').strip(),
            base_configuration_version=configuration.configuration_version,
            proposed_values=proposed_values, test_results=tests, proposed_by=request.user,
        )
        record_event(project=project, actor=request.user, action='schedule_defaults.proposed', entity=proposal, after=proposed_values, metadata={'test_results': tests})
        return Response(self.get_serializer(proposal).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def decision(self, request, pk=None):
        proposal = self.get_object()
        decision = request.data.get('decision')
        comment = (request.data.get('comment') or '').strip()
        if decision not in {'approved', 'rejected'}:
            raise ValidationError({'decision': 'Decision must be approved or rejected.'})
        if not can_final_approve_defaults(request.user, proposal.project):
            raise PermissionDenied('Only a project manager, project owner, or administrator can make the final decision.')
        if decision == 'rejected' and not comment:
            raise ValidationError({'comment': 'A rejection reason is required.'})
        with transaction.atomic():
            proposal = ScheduleDefaultProposal.objects.select_for_update().select_related('configuration', 'project').get(pk=proposal.pk)
            configuration = ProjectScheduleConfiguration.objects.select_for_update().get(pk=proposal.configuration_id)
            if proposal.status != 'proposed':
                raise ValidationError({'detail': 'This proposal already has a final decision.'})
            if decision == 'approved':
                if configuration.configuration_version != proposal.base_configuration_version:
                    raise ValidationError({'detail': 'The effective configuration changed after this proposal was created. Submit a new proposal.'})
                if not proposal.test_results or any(row.get('status') != 'passed' for row in proposal.test_results):
                    raise ValidationError({'detail': 'All Phase E acceptance tests must pass before approval.'})
                values = proposal.proposed_values
                configuration.workflow_template_id = values['workflow_template']
                configuration.dependency_template_id = values.get('dependency_template')
                configuration.standard_task_count = values['standard_task_count']
                configuration.settings = values.get('settings') or {}
                configuration.configuration_version += 1
                configuration.updated_by = request.user
                configuration.save()
            proposal.status = decision
            proposal.decided_by = request.user
            proposal.decided_at = timezone.now()
            proposal.decision_comment = comment
            proposal.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_comment', 'updated_at'])
            if decision == 'approved':
                ScheduleDefaultProposal.objects.filter(
                    project=proposal.project, status='proposed', is_deleted=False,
                ).exclude(pk=proposal.pk).update(status='superseded', updated_at=timezone.now())
        record_event(project=proposal.project, actor=request.user, action=f'schedule_defaults.{decision}', entity=proposal, after={'comment': comment, 'configuration_version': configuration.configuration_version})
        return Response(self.get_serializer(proposal).data)


class WorkflowTemplateOverrideViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowTemplateOverrideSerializer
    queryset = WorkflowTemplateOverride.objects.filter(is_deleted=False).select_related(
        'configuration__project', 'workflow_template',
    )

    def get_queryset(self):
        queryset = self.queryset.filter(configuration__project__in=accessible_projects(self.request.user))
        configuration_id = self.request.query_params.get('configuration')
        return queryset.filter(configuration_id=configuration_id) if configuration_id else queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['configuration'].project
        if not can_write_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project configuration.')
        override = serializer.save()
        record_event(project=project, actor=self.request.user, action='workflow_override.created', entity=override)

    def perform_update(self, serializer):
        project = serializer.instance.configuration.project
        if not can_write_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project configuration.')
        override = serializer.save()
        record_event(project=project, actor=self.request.user, action='workflow_override.updated', entity=override)

    def perform_destroy(self, instance):
        project = instance.configuration.project
        if not can_write_project(self.request.user, project):
            raise PermissionDenied('You cannot modify this project configuration.')
        instance.is_active = False
        instance.save(update_fields=['is_active', 'updated_at'])
        record_event(project=project, actor=self.request.user, action='workflow_override.deactivated', entity=instance)
