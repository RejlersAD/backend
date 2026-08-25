"""Secured REST endpoints for calendars, CPM schedules, resources, and baselines."""
import hashlib
from django.db import transaction
from django.http import Http404, HttpResponse
from django.db.models import Count, IntegerField, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import ValidationError
from rest_framework.throttling import ScopedRateThrottle

from apps.users.models import User

from .access import (
    PlanningObjectPermission, accessible_projects, can_final_approve_defaults, can_write_project,
)
from .governance_serializers import (
    GovernanceCommentInputSerializer, GovernanceCommentSerializer,
    GovernanceItemInputSerializer, GovernanceItemSerializer, GovernanceItemUpdateSerializer,
    GovernanceUserSerializer, ReviewDecisionInputSerializer, ScheduleReviewInputSerializer,
    ScheduleReviewSerializer,
)
from .models import (
    ActivityAssignment, ActivityProgressUpdate, ActivityRelationship, CalendarException, DailyFieldUpdate,
    GovernanceComment, GovernanceItem, Schedule,
    ScheduleActivity, ScheduleBaseline, ScheduleCalculationRun, ScheduleResource,
    ScheduleExportRecord, ScheduleReview, ScheduleReviewDecision, ScheduleVersion,
    ScheduleWBSNode, WorkCalendar,
)
from .serializers import PlanningAuditEventSerializer
from .schedule_serializers import (
    ActivityAssignmentSerializer, ActivityProgressUpdateSerializer, ActivityRelationshipSerializer,
    BulkActivityEditSerializer, BulkProgressUpdateSerializer, CalendarExceptionSerializer, ControlDateSerializer,
    DailyFieldUpdateSerializer,
    ScheduleActivitySerializer, ScheduleBaselineSerializer,
    ScheduleCalculationRunSerializer, ScheduleResourceSerializer, ScheduleSerializer,
    ScheduleControlSnapshotSerializer, ScheduleVersionSerializer, ScheduleWBSNodeSerializer,
    WorkCalendarSerializer,
)
from .services.audit import record_event
from .services.cpm import SchedulingError, calculate_schedule_version
from .services.project_controls import build_control_dashboard, capture_control_snapshot
from .services.schedule_exports import generate_schedule_export


def _build_deliverable_summaries(activities):
    """Roll workflow tasks up for a Primavera-style deliverable row."""
    grouped = {}
    for activity in activities:
        deliverable = (activity.metadata or {}).get('deliverable')
        if not deliverable:
            continue
        key = (activity.discipline, deliverable)
        summary = grouped.setdefault(key, {
            'discipline': activity.discipline, 'deliverable': deliverable,
            'planned_start': None, 'planned_finish': None, 'task_count': 0,
            'workflow_stages': [], 'critical_task_count': 0,
        })
        summary['task_count'] += 1
        stage = (activity.metadata or {}).get('workflow_stage_code')
        if stage:
            summary['workflow_stages'].append(stage)
        if activity.is_critical:
            summary['critical_task_count'] += 1
        if activity.planned_start and (
            summary['planned_start'] is None or activity.planned_start < summary['planned_start']
        ):
            summary['planned_start'] = activity.planned_start
        if activity.planned_finish and (
            summary['planned_finish'] is None or activity.planned_finish > summary['planned_finish']
        ):
            summary['planned_finish'] = activity.planned_finish
    return list(grouped.values())


class SoftDeleteViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]

    def perform_destroy(self, instance):
        instance.soft_delete()


def _mark_version_draft(version):
    version.status = 'draft'
    version.calculated_at = None
    version.calculated_finish = None
    version.save(update_fields=['status', 'calculated_at', 'calculated_finish', 'updated_at'])


def _governance_user_rows(project):
    role_by_user = {}
    if project.enterprise_project_id:
        enterprise = project.enterprise_project
        role_by_user[enterprise.owner_id] = 'project_manager'
        for membership in enterprise.memberships.filter(is_active=True).select_related('user'):
            role_by_user[membership.user_id] = membership.role
    elif project.created_by_id:
        role_by_user[project.created_by_id] = 'project_manager'
    users = User.objects.filter(id__in=role_by_user).order_by('first_name', 'last_name', 'email')
    rows = []
    for user in users:
        row = GovernanceUserSerializer(user).data
        row['role'] = role_by_user[user.id]
        rows.append(row)
    return rows, set(role_by_user)


def _require_governance_access(user, version):
    if not accessible_projects(user).filter(pk=version.schedule.project_id).exists():
        raise Http404


class WorkCalendarViewSet(SoftDeleteViewSet):
    serializer_class = WorkCalendarSerializer
    queryset = WorkCalendar.objects.filter(is_deleted=False).select_related('project')

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user)).prefetch_related('exceptions')
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def perform_create(self, serializer):
        calendar = serializer.save()
        if calendar.is_default:
            WorkCalendar.objects.filter(project=calendar.project, is_default=True).exclude(pk=calendar.pk).update(is_default=False)

    def perform_update(self, serializer):
        calendar = serializer.save()
        if calendar.is_default:
            WorkCalendar.objects.filter(project=calendar.project, is_default=True).exclude(pk=calendar.pk).update(is_default=False)


class CalendarExceptionViewSet(SoftDeleteViewSet):
    serializer_class = CalendarExceptionSerializer
    queryset = CalendarException.objects.filter(is_deleted=False).select_related('calendar__project')

    def get_queryset(self):
        queryset = super().get_queryset().filter(calendar__project__in=accessible_projects(self.request.user))
        calendar_id = self.request.query_params.get('calendar')
        return queryset.filter(calendar_id=calendar_id) if calendar_id else queryset


class ScheduleViewSet(SoftDeleteViewSet):
    serializer_class = ScheduleSerializer
    queryset = Schedule.objects.filter(is_deleted=False)

    def get_queryset(self):
        active_versions = (
            ScheduleVersion.objects.filter(schedule_id=OuterRef('pk'), is_deleted=False)
            .order_by()
            .values('schedule_id')
            .annotate(total=Count('id'))
            .values('total')[:1]
        )
        queryset = (
            super().get_queryset().filter(project__in=accessible_projects(self.request.user))
            .annotate(version_count=Coalesce(
                Subquery(active_versions, output_field=IntegerField()), Value(0),
            ))
            .select_related('project', 'default_calendar', 'created_by')
            .order_by('-created_at')
        )
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def perform_create(self, serializer):
        schedule = serializer.save(created_by=self.request.user)
        record_event(project=schedule.project, actor=self.request.user, action='schedule.created', entity=schedule)

    @action(detail=True, methods=['post'], url_path='create-version')
    def create_version(self, request, pk=None):
        """Clone the latest version so approved/baselined history remains immutable."""
        schedule = self.get_object()
        summary = str(request.data.get('change_summary') or 'Planner revision')[:255]
        with transaction.atomic():
            schedule = Schedule.objects.select_for_update().get(pk=schedule.pk)
            source = schedule.versions.filter(is_deleted=False).order_by('-version').first()
            next_number = (schedule.versions.aggregate(value=Max('version'))['value'] or 0) + 1
            version = ScheduleVersion.objects.create(
                schedule=schedule, version=next_number, parent_version=source,
                change_summary=summary, created_by=request.user,
            )
            if source:
                node_map = {}
                source_nodes = list(source.wbs_nodes.filter(is_deleted=False).order_by('sort_order'))
                for node in source_nodes:
                    node_map[node.pk] = ScheduleWBSNode.objects.create(
                        version=version, code=node.code, name=node.name, level=node.level,
                        sort_order=node.sort_order, discipline=node.discipline,
                    )
                for node in source_nodes:
                    if node.parent_id in node_map:
                        clone = node_map[node.pk]
                        clone.parent = node_map[node.parent_id]
                        clone.save(update_fields=['parent', 'updated_at'])
                activity_map = {}
                for item in source.activities.filter(is_deleted=False).order_by('sort_order'):
                    clone = ScheduleActivity.objects.create(
                        version=version, wbs_node=node_map.get(item.wbs_node_id), calendar=item.calendar,
                        external_id=item.external_id, name=item.name, activity_type=item.activity_type,
                        duration_days=item.duration_days, discipline=item.discipline,
                        responsible_role=item.responsible_role, constraint_type=item.constraint_type,
                        constraint_date=item.constraint_date, sort_order=item.sort_order, metadata=item.metadata,
                    )
                    activity_map[item.pk] = clone
                for link in source.relationships.filter(is_deleted=False):
                    if link.predecessor_id in activity_map and link.successor_id in activity_map:
                        ActivityRelationship.objects.create(
                            version=version, predecessor=activity_map[link.predecessor_id],
                            successor=activity_map[link.successor_id],
                            relationship_type=link.relationship_type, lag_days=link.lag_days,
                        )
                for assignment in ActivityAssignment.objects.filter(activity__version=source, is_deleted=False):
                    if assignment.activity_id in activity_map:
                        ActivityAssignment.objects.create(
                            activity=activity_map[assignment.activity_id], resource=assignment.resource,
                            planned_units=assignment.planned_units, budgeted_hours=assignment.budgeted_hours,
                            budgeted_cost=assignment.budgeted_cost,
                        )
        record_event(project=schedule.project, actor=request.user, action='schedule.version_created', entity=version, after={'version': version.version})
        return Response(ScheduleVersionSerializer(version).data, status=status.HTTP_201_CREATED)


class ScheduleVersionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = ScheduleVersionSerializer
    queryset = ScheduleVersion.objects.filter(is_deleted=False).select_related('schedule__project', 'source_generation')
    throttle_scope = 'planning_exports'

    def get_queryset(self):
        queryset = (
            super().get_queryset().filter(schedule__project__in=accessible_projects(self.request.user))
            .annotate(
                activity_count=Count('activities', filter=Q(activities__is_deleted=False), distinct=True),
                relationship_count=Count('relationships', filter=Q(relationships__is_deleted=False), distinct=True),
            )
        )
        schedule_id = self.request.query_params.get('schedule')
        return queryset.filter(schedule_id=schedule_id) if schedule_id else queryset

    @action(detail=True, methods=['get'])
    def workspace(self, request, pk=None):
        """Return the complete planner workspace in one consistent response."""
        version = self.get_object()
        schedule = version.schedule
        project = schedule.project
        intelligence_run = project.intelligence_runs.filter(is_deleted=False, status='succeeded').first()
        activities = list(
            version.activities.filter(is_deleted=False).select_related('wbs_node', 'calendar')
        )
        configuration = getattr(project, 'schedule_configuration', None)
        confirmed_rule_ids = {
            int(value) for value in ((configuration.settings or {}).get('confirmed_dependency_rule_ids', []) if configuration else [])
            if str(value).isdigit()
        }
        workflow_stages = list(configuration.workflow_template.stages.filter(
            is_deleted=False,
        ).order_by('sequence').values(
            'sequence', 'code', 'name', 'duration_days', 'responsible_party',
            'activity_type', 'relationship_to_previous', 'lag_days', 'progress_weight',
        )) if configuration else []
        dependency_template = configuration.dependency_template if configuration else None
        source_generation = version.source_generation
        dependency_assumptions = [
            item for item in (source_generation.logic_matrix or [])
            if item.get('source') == 'dependency_template'
        ] if source_generation else []
        return Response({
            'project': {
                'id': project.id, 'name': project.name, 'client': project.client,
                'location': project.location, 'phase': project.phase,
            },
            'schedule': ScheduleSerializer(schedule).data,
            'version': self.get_serializer(version).data,
            'calendar': WorkCalendarSerializer(schedule.default_calendar).data if schedule.default_calendar else None,
            'wbs': ScheduleWBSNodeSerializer(
                version.wbs_nodes.filter(is_deleted=False).select_related('parent'), many=True,
            ).data,
            'activities': ScheduleActivitySerializer(activities, many=True).data,
            'deliverable_summaries': _build_deliverable_summaries(activities),
            'relationships': ActivityRelationshipSerializer(
                version.relationships.filter(is_deleted=False).select_related('predecessor', 'successor'), many=True,
            ).data,
            'resources': ScheduleResourceSerializer(project.schedule_resources.filter(is_deleted=False), many=True).data,
            'assignments': ActivityAssignmentSerializer(
                ActivityAssignment.objects.filter(activity__version=version, is_deleted=False).select_related('resource'),
                many=True,
            ).data,
            'baselines': ScheduleBaselineSerializer(schedule.baselines.filter(is_deleted=False), many=True).data,
            'calculation_runs': ScheduleCalculationRunSerializer(
                version.calculation_runs.filter(is_deleted=False)[:10], many=True,
            ).data,
            'intelligence': {
                'run_id': intelligence_run.id,
                'fact_count': intelligence_run.fact_count,
                'conflict_count': intelligence_run.conflicts.filter(is_deleted=False, status='open').count(),
            } if intelligence_run else None,
            'scheduling_configuration': {
                'configuration_version': configuration.configuration_version,
                'workflow_template': configuration.workflow_template.code,
                'workflow_template_version': configuration.workflow_template.version,
                'standard_task_count': configuration.standard_task_count,
                'workflow_stages': workflow_stages,
                'dependency_template': dependency_template.code if dependency_template else None,
                'dependency_template_version': dependency_template.version if dependency_template else None,
                'dependency_rule_count': dependency_template.rules.filter(is_deleted=False).count() if dependency_template else 0,
                'confirmed_dependency_rule_count': len(confirmed_rule_ids),
                'date_authority': (configuration.settings or {}).get('date_authority', 'relational_cpm'),
            } if configuration else None,
            'generation_validation': source_generation.validation if source_generation else [],
            'dependency_assumptions': dependency_assumptions,
            'can_edit': can_write_project(request.user, project)
            and version.status not in {'approved', 'baselined', 'superseded'},
            'can_control': can_write_project(request.user, project) and version.status != 'superseded',
            'can_approve_field_updates': can_final_approve_defaults(request.user, project)
            and version.status != 'superseded',
        })

    @action(detail=True, methods=['get'], url_path='export', throttle_classes=[ScopedRateThrottle])
    def export(self, request, pk=None):
        version = self.get_object()
        export_format = str(request.query_params.get('export_format') or 'xlsx').lower()
        try:
            content, content_type, filename = generate_schedule_export(version, export_format)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        digest = hashlib.sha256(content).hexdigest()
        record = ScheduleExportRecord.objects.create(
            version=version, export_format=export_format, filename=filename,
            size_bytes=len(content), sha256=digest, requested_by=request.user,
        )
        record_event(
            project=version.schedule.project, actor=request.user, action='schedule.exported', entity=record,
            after={'version_id': version.id, 'format': export_format, 'sha256': digest},
        )
        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['X-Content-SHA256'] = digest
        return response

    @action(detail=True, methods=['patch'], url_path='bulk-activities')
    def bulk_activities(self, request, pk=None):
        """Optimistically locked, transactional saves from the activity grid."""
        version = self.get_object()
        request_serializer = BulkActivityEditSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            version = ScheduleVersion.objects.select_for_update().get(pk=version.pk)
            if version.status in {'approved', 'baselined', 'superseded'}:
                return Response({'error': 'This version is immutable.'}, status=status.HTTP_409_CONFLICT)
            expected = request_serializer.validated_data['expected_updated_at']
            if version.updated_at != expected:
                return Response({
                    'error': 'The schedule changed after you opened it. Reload before saving.',
                    'code': 'version_conflict', 'current_updated_at': version.updated_at,
                }, status=status.HTTP_409_CONFLICT)
            rows = request_serializer.validated_data['activities']
            activity_map = {
                item.id: item for item in version.activities.select_for_update().filter(
                    is_deleted=False, id__in=[row['id'] for row in rows],
                )
            }
            if len(activity_map) != len(rows):
                return Response(
                    {'error': 'One or more activities do not belong to this version.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            serializers_to_save = []
            for row in rows:
                row = dict(row)
                activity_id = row.pop('id')
                serializer = ScheduleActivitySerializer(
                    activity_map[activity_id], data=row, partial=True, context={'request': request},
                )
                serializer.is_valid(raise_exception=True)
                serializers_to_save.append(serializer)
            saved = [serializer.save() for serializer in serializers_to_save]
            _mark_version_draft(version)
            version.refresh_from_db()
        record_event(
            project=version.schedule.project, actor=request.user,
            action='schedule.activities_updated', entity=version,
            after={'activity_ids': [item.id for item in saved]},
        )
        return Response({
            'version': self.get_serializer(version).data,
            'activities': ScheduleActivitySerializer(saved, many=True).data,
        })

    @action(detail=True, methods=['post'])
    def calculate(self, request, pk=None):
        version = self.get_object()
        if version.status in {'approved', 'baselined', 'superseded'}:
            return Response({'error': 'This version is immutable.'}, status=status.HTTP_409_CONFLICT)
        try:
            run = calculate_schedule_version(version, requested_by=request.user)
        except SchedulingError as exc:
            return Response({'error': str(exc), 'code': exc.code, 'issues': exc.issues}, status=status.HTTP_400_BAD_REQUEST)
        record_event(
            project=version.schedule.project, actor=request.user, action='schedule.calculated', entity=version,
            after={'run_id': run.id, 'finish': run.project_finish.isoformat() if run.project_finish else None},
        )
        return Response(ScheduleCalculationRunSerializer(run).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        version = self.get_object()
        if version.status != 'calculated':
            return Response({'error': 'Only a calculated version can be approved.'}, status=status.HTTP_409_CONFLICT)
        generation = version.source_generation
        critical_findings = [
            item for item in (generation.validation or []) if item.get('severity') == 'critical'
        ] if generation else []
        unconfirmed_gates = [
            item for item in (generation.logic_matrix or [])
            if item.get('source') == 'dependency_template' and item.get('requires_confirmation')
        ] if generation else []
        if critical_findings or unconfirmed_gates:
            return Response({
                'error': 'Resolve critical generation findings and confirm engineering release gates before approval.',
                'code': 'schedule_assurance_blocked',
                'critical_finding_count': len(critical_findings),
                'unconfirmed_gate_count': len(unconfirmed_gates),
            }, status=status.HTTP_409_CONFLICT)
        version.status = 'approved'
        version.save(update_fields=['status', 'updated_at'])
        record_event(project=version.schedule.project, actor=request.user, action='schedule.approved', entity=version)
        return Response(self.get_serializer(version).data)

    @action(detail=True, methods=['get'], url_path='controls')
    def controls(self, request, pk=None):
        version = self.get_object()
        data_date = request.query_params.get('data_date')
        if data_date:
            serializer = ControlDateSerializer(data={'data_date': data_date})
            serializer.is_valid(raise_exception=True)
            data_date = serializer.validated_data['data_date']
        dashboard = build_control_dashboard(version, data_date)
        dashboard['snapshots'] = ScheduleControlSnapshotSerializer(
            version.control_snapshots.filter(is_deleted=False)[:12], many=True,
        ).data
        return Response(dashboard)

    @action(detail=True, methods=['post'], url_path='progress')
    def progress(self, request, pk=None):
        version = self.get_object()
        if not can_write_project(request.user, version.schedule.project):
            return Response({'error': 'You cannot update progress for this project.'}, status=status.HTTP_403_FORBIDDEN)
        if version.status == 'superseded':
            return Response({'error': 'Progress cannot be posted to a superseded version.'}, status=status.HTTP_409_CONFLICT)
        serializer = BulkProgressUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data_date = serializer.validated_data['data_date']
        rows = serializer.validated_data['updates']
        activity_map = {
            item.id: item for item in version.activities.filter(
                is_deleted=False, id__in=[row['activity'] for row in rows],
            )
        }
        if len(activity_map) != len(rows):
            return Response({'error': 'One or more activities do not belong to this version.'}, status=status.HTTP_400_BAD_REQUEST)
        for row in rows:
            if row.get('actual_start') and row['actual_start'] > data_date:
                return Response({'error': 'Actual start cannot be after the data date.'}, status=status.HTTP_400_BAD_REQUEST)
            if row.get('actual_finish') and row['actual_finish'] > data_date:
                return Response({'error': 'Actual finish cannot be after the data date.'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            saved = []
            for row in rows:
                values = dict(row)
                activity_id = values.pop('activity')
                update, _ = ActivityProgressUpdate.objects.update_or_create(
                    activity=activity_map[activity_id], data_date=data_date,
                    defaults={
                        **values, 'version': version, 'reported_by': request.user,
                        'is_deleted': False, 'deleted_at': None,
                    },
                )
                saved.append(update)
            version.schedule.data_date = data_date
            version.schedule.save(update_fields=['data_date', 'updated_at'])
        record_event(
            project=version.schedule.project, actor=request.user, action='schedule.progress_updated', entity=version,
            after={'data_date': data_date.isoformat(), 'activity_ids': list(activity_map)},
        )
        return Response({
            'data_date': data_date,
            'updates': ActivityProgressUpdateSerializer(saved, many=True).data,
            'controls': build_control_dashboard(version, data_date),
        })

    @action(detail=True, methods=['post'], url_path='capture-controls')
    def capture_controls(self, request, pk=None):
        version = self.get_object()
        if not can_write_project(request.user, version.schedule.project):
            return Response({'error': 'You cannot capture controls for this project.'}, status=status.HTTP_403_FORBIDDEN)
        if version.status == 'superseded':
            return Response({'error': 'Controls cannot be captured for a superseded version.'}, status=status.HTTP_409_CONFLICT)
        serializer = ControlDateSerializer(data={
            'data_date': request.data.get('data_date') or version.schedule.data_date or timezone.localdate(),
        })
        serializer.is_valid(raise_exception=True)
        snapshot = capture_control_snapshot(version, serializer.validated_data['data_date'], request.user)
        record_event(
            project=version.schedule.project, actor=request.user, action='schedule.controls_captured',
            entity=snapshot, after={'data_date': snapshot.data_date.isoformat()},
        )
        return Response(ScheduleControlSnapshotSerializer(snapshot).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='governance', permission_classes=[IsAuthenticated])
    def governance(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        project = version.schedule.project
        members, _ = _governance_user_rows(project)
        items = version.governance_items.filter(is_deleted=False).select_related(
            'activity', 'owner', 'raised_by',
        ).prefetch_related('comments__author', 'comments__resolved_by')
        reviews = version.governance_reviews.filter(is_deleted=False).select_related(
            'requested_by',
        ).prefetch_related('decisions__reviewer', 'comments__author', 'comments__resolved_by')
        audit = project.audit_events.select_related('actor')[:40]
        return Response({
            'version': self.get_serializer(version).data,
            'current_user_id': request.user.id,
            'can_manage': can_write_project(request.user, project),
            'members': members,
            'items': GovernanceItemSerializer(items, many=True).data,
            'reviews': ScheduleReviewSerializer(reviews, many=True).data,
            'audit_events': PlanningAuditEventSerializer(audit, many=True).data,
            'summary': {
                'open_items': items.exclude(status__in=['closed', 'implemented', 'rejected']).count(),
                'critical_items': items.filter(priority='critical').exclude(status__in=['closed', 'implemented', 'rejected']).count(),
                'pending_reviews': reviews.filter(status='pending').count(),
                'unresolved_comments': GovernanceComment.objects.filter(
                    Q(item__version=version) | Q(review__version=version),
                    is_deleted=False, is_resolved=False,
                ).count(),
            },
        })

    @action(detail=True, methods=['post'], url_path='governance-items', permission_classes=[IsAuthenticated])
    def governance_items(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        project = version.schedule.project
        if not can_write_project(request.user, project):
            return Response({'error': 'Only project editors can raise governance items.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = GovernanceItemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        activity_id = values.pop('activity', None)
        owner_id = values.pop('owner', None)
        _, member_ids = _governance_user_rows(project)
        if owner_id and owner_id not in member_ids:
            return Response({'error': 'The owner must be an active project member.'}, status=status.HTTP_400_BAD_REQUEST)
        activity = None
        if activity_id:
            activity = version.activities.filter(pk=activity_id, is_deleted=False).first()
            if not activity:
                return Response({'error': 'The activity does not belong to this version.'}, status=status.HTTP_400_BAD_REQUEST)
        item = GovernanceItem.objects.create(
            version=version, activity=activity, owner_id=owner_id,
            raised_by=request.user, **values,
        )
        record_event(
            project=project, actor=request.user, action='governance.item_created', entity=item,
            after={'title': item.title, 'type': item.item_type, 'version_id': version.id},
        )
        return Response(GovernanceItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch'], url_path='governance-item', permission_classes=[IsAuthenticated])
    def governance_item(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        project = version.schedule.project
        if not can_write_project(request.user, project):
            return Response({'error': 'Only project editors can update governance items.'}, status=status.HTTP_403_FORBIDDEN)
        item = version.governance_items.filter(pk=request.data.get('item_id'), is_deleted=False).first()
        if not item:
            return Response({'error': 'Governance item not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = GovernanceItemUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        before = {'status': item.status, 'owner_id': item.owner_id, 'due_date': item.due_date}
        owner_was_provided = 'owner' in values
        owner_id = values.pop('owner', None)
        if owner_was_provided:
            _, member_ids = _governance_user_rows(project)
            if owner_id and owner_id not in member_ids:
                return Response({'error': 'The owner must be an active project member.'}, status=status.HTTP_400_BAD_REQUEST)
            item.owner_id = owner_id
        for field, value in values.items():
            setattr(item, field, value)
        if item.status in {'closed', 'implemented', 'rejected'}:
            item.closed_at = item.closed_at or timezone.now()
        else:
            item.closed_at = None
        item.save()
        record_event(
            project=project, actor=request.user, action='governance.item_updated', entity=item,
            before=before, after={'status': item.status, 'owner_id': item.owner_id, 'version_id': version.id},
        )
        return Response(GovernanceItemSerializer(item).data)

    @action(detail=True, methods=['post'], url_path='governance-comments', permission_classes=[IsAuthenticated])
    def governance_comments(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        serializer = GovernanceCommentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        item_id = values.pop('item', None)
        review_id = values.pop('review', None)
        parent_id = values.pop('parent', None)
        item = version.governance_items.filter(pk=item_id, is_deleted=False).first() if item_id else None
        review = version.governance_reviews.filter(pk=review_id, is_deleted=False).first() if review_id else None
        if not item and not review:
            return Response({'error': 'The discussion target does not belong to this version.'}, status=status.HTTP_400_BAD_REQUEST)
        _, member_ids = _governance_user_rows(version.schedule.project)
        if set(values['mentioned_user_ids']) - member_ids:
            return Response({'error': 'Mentions must reference active project members.'}, status=status.HTTP_400_BAD_REQUEST)
        parent = None
        if parent_id:
            parent = GovernanceComment.objects.filter(pk=parent_id, is_deleted=False).first()
            if not parent or parent.item_id != (item.id if item else None) or parent.review_id != (review.id if review else None):
                return Response({'error': 'The reply target is outside this discussion.'}, status=status.HTTP_400_BAD_REQUEST)
        comment = GovernanceComment.objects.create(
            item=item, review=review, parent=parent, author=request.user, **values,
        )
        record_event(
            project=version.schedule.project, actor=request.user, action='governance.comment_added', entity=comment,
            after={'version_id': version.id, 'item_id': item_id, 'review_id': review_id},
        )
        return Response(GovernanceCommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='resolve-governance-comment', permission_classes=[IsAuthenticated])
    def resolve_governance_comment(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        comment = GovernanceComment.objects.filter(
            Q(item__version=version) | Q(review__version=version),
            pk=request.data.get('comment_id'), is_deleted=False,
        ).first()
        if not comment:
            return Response({'error': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)
        if comment.author_id != request.user.id and not can_write_project(request.user, version.schedule.project):
            return Response({'error': 'Only the author or a project editor can resolve this comment.'}, status=status.HTTP_403_FORBIDDEN)
        comment.is_resolved = bool(request.data.get('is_resolved', True))
        comment.resolved_by = request.user if comment.is_resolved else None
        comment.resolved_at = timezone.now() if comment.is_resolved else None
        comment.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at', 'updated_at'])
        record_event(
            project=version.schedule.project, actor=request.user, action='governance.comment_resolved', entity=comment,
            after={'version_id': version.id, 'is_resolved': comment.is_resolved},
        )
        return Response(GovernanceCommentSerializer(comment).data)

    @action(detail=True, methods=['post'], url_path='reviews', permission_classes=[IsAuthenticated])
    def reviews(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        project = version.schedule.project
        if not can_write_project(request.user, project):
            return Response({'error': 'Only project editors can request a review.'}, status=status.HTTP_403_FORBIDDEN)
        if version.status != 'calculated':
            return Response({'error': 'Calculate the schedule before requesting approval.'}, status=status.HTTP_409_CONFLICT)
        if version.governance_reviews.filter(status='pending', is_deleted=False).exists():
            return Response({'error': 'This version already has a pending review.'}, status=status.HTTP_409_CONFLICT)
        serializer = ScheduleReviewInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        _, member_ids = _governance_user_rows(project)
        reviewer_ids = set(values['reviewer_ids'])
        if reviewer_ids - member_ids:
            return Response({'error': 'All reviewers must be active project members.'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            review = ScheduleReview.objects.create(
                version=version, title=values['title'], description=values['description'],
                due_date=values.get('due_date'), requested_by=request.user, requested_at=timezone.now(),
            )
            ScheduleReviewDecision.objects.bulk_create([
                ScheduleReviewDecision(review=review, reviewer_id=user_id) for user_id in reviewer_ids
            ])
        record_event(
            project=project, actor=request.user, action='governance.review_requested', entity=review,
            after={'version_id': version.id, 'reviewer_ids': sorted(reviewer_ids)},
        )
        return Response(ScheduleReviewSerializer(review).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='review-decision', permission_classes=[IsAuthenticated])
    def review_decision(self, request, pk=None):
        version = self.get_object()
        _require_governance_access(request.user, version)
        review = version.governance_reviews.filter(pk=request.data.get('review_id'), is_deleted=False).first()
        if not review:
            return Response({'error': 'Review not found.'}, status=status.HTTP_404_NOT_FOUND)
        if review.status != 'pending':
            return Response({'error': 'This review is already complete.'}, status=status.HTTP_409_CONFLICT)
        decision = review.decisions.filter(reviewer=request.user, is_deleted=False).first()
        if not decision and not (request.user.is_staff or request.user.is_superuser):
            return Response({'error': 'You are not assigned to this review.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = ReviewDecisionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not decision:
            decision = review.decisions.filter(is_deleted=False, status='pending').first()
        decision.status = serializer.validated_data['decision']
        decision.comment = serializer.validated_data['comment']
        decision.decided_at = timezone.now()
        decision.save(update_fields=['status', 'comment', 'decided_at', 'updated_at'])
        statuses = list(review.decisions.filter(is_deleted=False).values_list('status', flat=True))
        if 'rejected' in statuses:
            review.status = 'rejected'
        elif 'changes_requested' in statuses:
            review.status = 'changes_requested'
        elif statuses and all(value == 'approved' for value in statuses):
            review.status = 'approved'
            if version.status == 'calculated':
                version.status = 'approved'
                version.save(update_fields=['status', 'updated_at'])
        if review.status != 'pending':
            review.completed_at = timezone.now()
        review.save(update_fields=['status', 'completed_at', 'updated_at'])
        record_event(
            project=version.schedule.project, actor=request.user, action='governance.review_decided', entity=review,
            after={'version_id': version.id, 'decision': decision.status, 'review_status': review.status},
        )
        return Response(ScheduleReviewSerializer(review).data)

    @action(detail=True, methods=['post'])
    def baseline(self, request, pk=None):
        version = self.get_object()
        if version.status not in {'calculated', 'approved'}:
            return Response({'error': 'Calculate the version before creating a baseline.'}, status=status.HTTP_409_CONFLICT)
        name = str(request.data.get('name') or f'Baseline {version.version}')[:255]
        if version.schedule.baselines.filter(name=name, is_deleted=False).exists():
            return Response({'error': 'A baseline with this name already exists.'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            version = ScheduleVersion.objects.select_for_update().get(pk=version.pk)
            if version.status not in {'calculated', 'approved'}:
                return Response({'error': 'The version is no longer available for baselining.'}, status=status.HTTP_409_CONFLICT)
            snapshot = {
                'version': ScheduleVersionSerializer(version).data,
                'wbs': ScheduleWBSNodeSerializer(version.wbs_nodes.filter(is_deleted=False), many=True).data,
                'activities': ScheduleActivitySerializer(version.activities.filter(is_deleted=False), many=True).data,
                'relationships': ActivityRelationshipSerializer(version.relationships.filter(is_deleted=False), many=True).data,
            }
            baseline = ScheduleBaseline.objects.create(
                schedule=version.schedule, source_version=version, name=name,
                data_date=version.schedule.data_date, snapshot=snapshot,
                approved_by=request.user, approved_at=timezone.now(),
            )
            version.status = 'baselined'
            version.save(update_fields=['status', 'updated_at'])
        record_event(project=version.schedule.project, actor=request.user, action='schedule.baselined', entity=baseline, after={'version': version.version})
        return Response(ScheduleBaselineSerializer(baseline).data, status=status.HTTP_201_CREATED)


class VersionChildViewSet(SoftDeleteViewSet):
    version_path = 'version'
    filter_name = 'version'

    def get_queryset(self):
        queryset = super().get_queryset().filter(**{f'{self.version_path}__schedule__project__in': accessible_projects(self.request.user)})
        version_id = self.request.query_params.get('version')
        return queryset.filter(**{f'{self.filter_name}_id': version_id}) if version_id else queryset

    def perform_create(self, serializer):
        instance = serializer.save()
        _mark_version_draft(getattr(instance, self.filter_name))

    def perform_update(self, serializer):
        instance = serializer.save()
        _mark_version_draft(getattr(instance, self.filter_name))

    def perform_destroy(self, instance):
        version = getattr(instance, self.filter_name)
        if version.status in {'approved', 'baselined', 'superseded'}:
            raise ValidationError('Approved, baselined, and superseded schedule versions are immutable.')
        super().perform_destroy(instance)
        _mark_version_draft(version)


class ScheduleWBSNodeViewSet(VersionChildViewSet):
    serializer_class = ScheduleWBSNodeSerializer
    queryset = ScheduleWBSNode.objects.filter(is_deleted=False).select_related('version__schedule__project', 'parent')


class ScheduleActivityViewSet(VersionChildViewSet):
    serializer_class = ScheduleActivitySerializer
    queryset = ScheduleActivity.objects.filter(is_deleted=False).select_related('version__schedule__project', 'wbs_node', 'calendar')


class ActivityRelationshipViewSet(VersionChildViewSet):
    serializer_class = ActivityRelationshipSerializer
    queryset = ActivityRelationship.objects.filter(is_deleted=False).select_related('version__schedule__project', 'predecessor', 'successor')


class ScheduleResourceViewSet(SoftDeleteViewSet):
    serializer_class = ScheduleResourceSerializer
    queryset = ScheduleResource.objects.filter(is_deleted=False).select_related('project')

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset


class ActivityAssignmentViewSet(SoftDeleteViewSet):
    serializer_class = ActivityAssignmentSerializer
    queryset = ActivityAssignment.objects.filter(is_deleted=False).select_related('activity__version__schedule__project', 'resource')

    def get_queryset(self):
        queryset = super().get_queryset().filter(activity__version__schedule__project__in=accessible_projects(self.request.user))
        activity_id = self.request.query_params.get('activity')
        return queryset.filter(activity_id=activity_id) if activity_id else queryset

    def perform_create(self, serializer):
        values = serializer.validated_data
        assignment = ActivityAssignment.objects.filter(
            activity=values['activity'], resource=values['resource'], is_deleted=True,
        ).first()
        if assignment:
            for field, value in values.items():
                setattr(assignment, field, value)
            assignment.is_deleted = False
            assignment.deleted_at = None
            assignment.save()
            serializer.instance = assignment
        else:
            assignment = serializer.save()
        _mark_version_draft(assignment.activity.version)

    def perform_update(self, serializer):
        assignment = serializer.save()
        _mark_version_draft(assignment.activity.version)

    def perform_destroy(self, instance):
        if instance.activity.version.status in {'approved', 'baselined', 'superseded'}:
            raise ValidationError('Approved, baselined, and superseded schedule versions are immutable.')
        version = instance.activity.version
        super().perform_destroy(instance)
        _mark_version_draft(version)


class DailyFieldUpdateViewSet(SoftDeleteViewSet):
    serializer_class = DailyFieldUpdateSerializer
    queryset = DailyFieldUpdate.objects.filter(is_deleted=False).select_related(
        'version__schedule__project', 'activity', 'reported_by', 'reviewed_by',
        'applied_progress_update',
    )

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            version__schedule__project__in=accessible_projects(self.request.user),
        )
        for parameter, field in (
            ('version', 'version_id'), ('activity', 'activity_id'),
            ('status', 'status'), ('report_date', 'report_date'),
        ):
            value = self.request.query_params.get(parameter)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_create(self, serializer):
        activity = serializer.validated_data['activity']
        project = activity.version.schedule.project
        if not can_write_project(self.request.user, project):
            raise ValidationError('You cannot submit field updates for this project.')
        update = serializer.save(version=activity.version, reported_by=self.request.user)
        record_event(
            project=project, actor=self.request.user, action='field_update.created', entity=update,
            after={'activity_id': activity.id, 'report_date': update.report_date.isoformat()},
        )

    def perform_update(self, serializer):
        if serializer.instance.status not in {'draft', 'rejected'}:
            raise ValidationError('Only draft or rejected field updates can be edited.')
        update = serializer.save()
        record_event(
            project=update.version.schedule.project, actor=self.request.user,
            action='field_update.updated', entity=update,
        )

    def perform_destroy(self, instance):
        if instance.status not in {'draft', 'rejected'}:
            raise ValidationError('Only draft or rejected field updates can be deleted.')
        super().perform_destroy(instance)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        update = self.get_object()
        if not can_write_project(request.user, update.version.schedule.project):
            return Response({'error': 'You cannot submit this field update.'}, status=status.HTTP_403_FORBIDDEN)
        if update.status not in {'draft', 'rejected'}:
            return Response({'error': 'Only draft or rejected updates can be submitted.'}, status=status.HTTP_409_CONFLICT)
        update.status = 'submitted'
        update.submitted_at = timezone.now()
        update.reviewed_by = None
        update.reviewed_at = None
        update.review_comment = ''
        update.save(update_fields=[
            'status', 'submitted_at', 'reviewed_by', 'reviewed_at', 'review_comment', 'updated_at',
        ])
        record_event(
            project=update.version.schedule.project, actor=request.user,
            action='field_update.submitted', entity=update,
        )
        return Response(self.get_serializer(update).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        update = self.get_object()
        project = update.version.schedule.project
        if not can_final_approve_defaults(request.user, project):
            return Response({'error': 'Only the project manager can approve field progress.'}, status=status.HTTP_403_FORBIDDEN)
        if update.status != 'submitted':
            return Response({'error': 'Only submitted field updates can be approved.'}, status=status.HTTP_409_CONFLICT)

        comment = str(request.data.get('comment') or '').strip()[:4000]
        note_parts = [part for part in (
            update.notes,
            f'Field location: {update.work_location}' if update.work_location else '',
            f'Constraint: {update.constraints}' if update.constraints else '',
            f'Approved field update #{update.id}',
        ) if part]
        with transaction.atomic():
            progress, _ = ActivityProgressUpdate.objects.update_or_create(
                activity=update.activity, data_date=update.report_date,
                defaults={
                    'version': update.version,
                    'physical_progress_pct': update.physical_progress_pct,
                    'remaining_duration_days': update.remaining_duration_days,
                    'actual_start': update.actual_start,
                    'actual_finish': update.actual_finish,
                    'forecast_finish': update.forecast_finish,
                    'actual_hours': update.actual_hours,
                    'actual_cost': update.actual_cost,
                    'notes': '\n'.join(note_parts),
                    'reported_by': update.reported_by,
                    'is_deleted': False,
                    'deleted_at': None,
                },
            )
            schedule = update.version.schedule
            if schedule.data_date is None or update.report_date > schedule.data_date:
                schedule.data_date = update.report_date
                schedule.save(update_fields=['data_date', 'updated_at'])
            if update.constraints:
                GovernanceItem.objects.create(
                    version=update.version, activity=update.activity, item_type='issue',
                    title=f'Field constraint — {update.activity.external_id}',
                    description=update.constraints, status='open', priority='high',
                    raised_by=update.reported_by, metadata={'daily_field_update_id': update.id},
                )
            snapshot = capture_control_snapshot(update.version, update.report_date, request.user)
            update.status = 'approved'
            update.reviewed_by = request.user
            update.reviewed_at = timezone.now()
            update.review_comment = comment
            update.applied_progress_update = progress
            update.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'review_comment',
                'applied_progress_update', 'updated_at',
            ])
        record_event(
            project=project, actor=request.user, action='field_update.approved', entity=update,
            after={'progress_update_id': progress.id, 'snapshot_id': snapshot.id},
        )
        return Response({
            'field_update': self.get_serializer(update).data,
            'controls': build_control_dashboard(update.version, update.report_date),
            'snapshot': ScheduleControlSnapshotSerializer(snapshot).data,
        })

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        update = self.get_object()
        project = update.version.schedule.project
        if not can_final_approve_defaults(request.user, project):
            return Response({'error': 'Only the project manager can reject field progress.'}, status=status.HTTP_403_FORBIDDEN)
        if update.status != 'submitted':
            return Response({'error': 'Only submitted field updates can be rejected.'}, status=status.HTTP_409_CONFLICT)
        comment = str(request.data.get('comment') or '').strip()
        if not comment:
            return Response({'error': 'A rejection comment is required.'}, status=status.HTTP_400_BAD_REQUEST)
        update.status = 'rejected'
        update.reviewed_by = request.user
        update.reviewed_at = timezone.now()
        update.review_comment = comment[:4000]
        update.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_comment', 'updated_at'])
        record_event(
            project=project, actor=request.user, action='field_update.rejected', entity=update,
            after={'comment': update.review_comment},
        )
        return Response(self.get_serializer(update).data)


class ScheduleBaselineViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = ScheduleBaselineSerializer
    queryset = ScheduleBaseline.objects.filter(is_deleted=False).select_related('schedule__project', 'source_version', 'approved_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(schedule__project__in=accessible_projects(self.request.user))
        schedule_id = self.request.query_params.get('schedule')
        return queryset.filter(schedule_id=schedule_id) if schedule_id else queryset


class ScheduleCalculationRunViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = ScheduleCalculationRunSerializer
    queryset = ScheduleCalculationRun.objects.filter(is_deleted=False).select_related('version__schedule__project', 'requested_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(version__schedule__project__in=accessible_projects(self.request.user))
        version_id = self.request.query_params.get('version')
        return queryset.filter(version_id=version_id) if version_id else queryset
