from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.project_models import Project, ProjectMilestone
from apps.planning_intelligence.models import ScheduleActivity, ScheduleVersion
from apps.procurement.models import PurchaseOrder
from .access import accessible_enterprise_projects, can_write_enterprise_project
from .epc_models import IntegratedBaseline, WBSActivityLink, control_scope
from .execution_models import EPC_PHASES, EPCWorkItem
from .execution_serializers import EPCAcceptSerializer, EPCReviewSerializer, EPCWorkItemSerializer, person_name, project_people
from .models import ProjectDocument, WBSNode
from .services.execution import _event, accept_work, review_work, submit_work


class EPCWorkItemViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.UpdateModelMixin, viewsets.GenericViewSet):
    business_approval_actions = {'review', 'accept'}
    permission_classes = [IsAuthenticated]
    serializer_class = EPCWorkItemSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        rows = EPCWorkItem.objects.filter(is_deleted=False,
            project__in=accessible_enterprise_projects(self.request.user)).select_related(
                'project', 'owner', 'reviewer', 'wbs_node', 'activity__version', 'baseline__schedule_baseline', 'purchase_order'
            ).prefetch_related('documents', 'predecessors', 'events__actor')
        project_id = self.request.query_params.get('project')
        return rows.filter(project_id=project_id) if project_id else rows

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        project_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('project'))
        project = get_object_or_404(accessible_enterprise_projects(request.user), pk=project_id)
        Project.objects.select_for_update().get(pk=project.pk)
        if not request.user.is_active or not can_write_enterprise_project(request.user, project):
            raise PermissionDenied('Project write access is required.')
        self._lock_activities(request.data.get('activity'), project)
        return super().create(request, *args, **kwargs)

    def _lock_activities(self, activity_id, project, previous=None):
        ids = [previous] if previous else []
        if activity_id not in (None, ''):
            ids.append(serializers.IntegerField(min_value=1).run_validation(activity_id))
        version_ids = ScheduleActivity.objects.filter(pk__in=ids,
            version__schedule__project__enterprise_project=project).values('version_id')
        list(ScheduleVersion.objects.select_for_update().filter(pk__in=version_ids).order_by('id'))

    def perform_create(self, serializer):
        item = serializer.save()
        _event(item, 'created', self.request.user, payload={'code': item.code, 'phase': item.phase})

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        item = self.get_object()
        Project.objects.select_for_update().get(pk=item.project_id)
        if not request.user.is_active or not can_write_enterprise_project(request.user, item.project):
            raise PermissionDenied('Project write access is required.')
        self._lock_activities(request.data.get('activity'), item.project, item.activity_id)
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        item = serializer.save()
        _event(item, 'edited', self.request.user, payload={'fields': sorted(serializer.validated_data)})

    @action(detail=False, methods=['get'], url_path='options')
    def work_options(self, request):
        project_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get('project'))
        project = get_object_or_404(accessible_enterprise_projects(request.user), pk=project_id)
        scope = control_scope(project.scope_type)
        links = WBSActivityLink.objects.filter(project=project, is_deleted=False, link_type__in=scope['owned_phases'])
        from .services.epc import wbs_options, wbs_phase
        nodes = wbs_options(project)
        nodes_by_id = {row['id']: row for row in nodes}
        if project.scope_type == 'detailed_engineering':
            nodes = [row for row in nodes if wbs_phase(row['id'], nodes_by_id) == 'engineering']
            links = links.filter(wbs_node_id__in=[row['id'] for row in nodes])
        return Response({
            'control_scope': scope,
            'phases': [{'value': value, 'label': label} for value, label in EPC_PHASES if value in scope['owned_phases']],
            'wbs_nodes': nodes,
            'activities': list(ScheduleActivity.objects.filter(id__in=links.values('activity'), is_deleted=False,
                epc_work_item__isnull=True).exclude(version__status='superseded').values('id', 'external_id', 'name')),
            'documents': list(ProjectDocument.objects.filter(project=project, is_deleted=False).values('id', 'title', 'original_filename')),
            'purchase_orders': list(PurchaseOrder.objects.filter(enterprise_project=project).values('id', 'po_number')),
            'milestones': list(ProjectMilestone.objects.filter(project=project, is_deleted=False, is_completed=False,
                epc_work_item__isnull=True).values('id', 'name')),
            'people': [{'id': person.pk, 'name': person_name(person)} for person in project_people(project)],
            'predecessors': list(EPCWorkItem.objects.filter(project=project, is_deleted=False).values('id', 'code', 'title', 'status')),
            'baselines': list(IntegratedBaseline.objects.filter(project=project).values('id', 'revision', 'name')),
            'can_create': bool(request.user.is_active and can_write_enterprise_project(request.user, project)),
        })

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        item = submit_work(self.get_object(), user=request.user)
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        serializer = EPCReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = review_work(self.get_object(), user=request.user, **serializer.validated_data)
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        serializer = EPCAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = accept_work(self.get_object(), user=request.user, **serializer.validated_data)
        return Response(self.get_serializer(item).data)
