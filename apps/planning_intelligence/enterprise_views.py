"""APIs for schedule integrations, export audit, portfolio, and retention readiness."""
from __future__ import annotations

import datetime as dt

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .access import accessible_projects, can_write_project
from .enterprise_serializers import (
    IntegrationDeliverySerializer, IntegrationEndpointSerializer,
    PlanningRetentionPolicySerializer, ScheduleExportRecordSerializer,
)
from .models import (
    IntegrationDelivery, IntegrationEndpoint, PlanningJob, PlanningProject,
    PlanningRetentionPolicy, ScheduleExportRecord, ScheduleVersion,
)
from .services import integration_secrets
from .services.audit import record_event
from .tasks import deliver_schedule_integration


class IntegrationEndpointViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = IntegrationEndpointSerializer
    queryset = IntegrationEndpoint.objects.filter(is_deleted=False).select_related('project', 'created_by')
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'planning_integrations'

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['project']
        if not can_write_project(self.request.user, project):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You cannot configure integrations for this project.')
        secret = serializer.validated_data.pop('secret', '')
        if secret and not integration_secrets.is_configured():
            from rest_framework.exceptions import APIException
            exc = APIException('Integration credential encryption is not configured.')
            exc.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            raise exc
        endpoint = serializer.save(
            created_by=self.request.user,
            secret_encrypted=integration_secrets.encrypt_secret(secret) if secret else '',
        )
        record_event(
            project=project, actor=self.request.user, action='integration.endpoint_created', entity=endpoint,
            after={'name': endpoint.name, 'format': endpoint.export_format},
        )

    def perform_update(self, serializer):
        endpoint = serializer.instance
        if not can_write_project(self.request.user, endpoint.project):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You cannot modify this integration.')
        secret = serializer.validated_data.pop('secret', '')
        if secret:
            if not integration_secrets.is_configured():
                from rest_framework.exceptions import APIException
                exc = APIException('Integration credential encryption is not configured.')
                exc.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                raise exc
            serializer.validated_data['secret_encrypted'] = integration_secrets.encrypt_secret(secret)
        endpoint = serializer.save()
        record_event(project=endpoint.project, actor=self.request.user, action='integration.endpoint_updated', entity=endpoint)

    def perform_destroy(self, instance):
        if not can_write_project(self.request.user, instance.project):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You cannot remove this integration.')
        instance.soft_delete()
        record_event(project=instance.project, actor=self.request.user, action='integration.endpoint_archived', entity=instance)

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        endpoint = self.get_object()
        if not can_write_project(request.user, endpoint.project):
            return Response({'error': 'You cannot publish through this integration.'}, status=status.HTTP_403_FORBIDDEN)
        if not endpoint.is_active:
            return Response({'error': 'This integration is inactive.'}, status=status.HTTP_409_CONFLICT)
        version = ScheduleVersion.objects.filter(
            pk=request.data.get('version'), schedule__project=endpoint.project, is_deleted=False,
        ).first()
        if not version:
            return Response({'error': 'Schedule version not found for this project.'}, status=status.HTTP_400_BAD_REQUEST)
        event_type = str(request.data.get('event_type') or 'schedule.published')[:64]
        if endpoint.event_types and event_type not in endpoint.event_types:
            return Response({'error': 'This event type is not enabled for the integration.'}, status=status.HTTP_400_BAD_REQUEST)
        key = str(request.data.get('idempotency_key') or f'{event_type}:{version.id}:{version.updated_at.isoformat()}')[:255]
        delivery, created = IntegrationDelivery.objects.get_or_create(
            endpoint=endpoint, idempotency_key=key,
            defaults={'version': version, 'event_type': event_type, 'requested_by': request.user},
        )
        if created:
            try:
                deliver_schedule_integration.delay(delivery.id)
            except Exception:  # noqa: BLE001
                deliver_schedule_integration.apply(args=[delivery.id])
            delivery.refresh_from_db()
            record_event(
                project=endpoint.project, actor=request.user, action='integration.delivery_queued', entity=delivery,
                after={'version_id': version.id, 'event_type': event_type},
            )
        return Response(IntegrationDeliverySerializer(delivery).data, status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK)


class IntegrationDeliveryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = IntegrationDeliverySerializer
    queryset = IntegrationDelivery.objects.filter(is_deleted=False).select_related(
        'endpoint__project', 'version__schedule', 'requested_by',
    )
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'planning_integrations'

    def get_queryset(self):
        queryset = super().get_queryset().filter(endpoint__project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(endpoint__project_id=project_id) if project_id else queryset

    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        delivery = self.get_object()
        if not can_write_project(request.user, delivery.endpoint.project):
            return Response({'error': 'You cannot retry this delivery.'}, status=status.HTTP_403_FORBIDDEN)
        if delivery.status != 'failed':
            return Response({'error': 'Only failed deliveries can be retried.'}, status=status.HTTP_409_CONFLICT)
        delivery.status = 'queued'
        delivery.finished_at = None
        delivery.save(update_fields=['status', 'finished_at', 'updated_at'])
        try:
            deliver_schedule_integration.delay(delivery.id)
        except Exception:  # noqa: BLE001
            deliver_schedule_integration.apply(args=[delivery.id])
        delivery.refresh_from_db()
        return Response(self.get_serializer(delivery).data, status=status.HTTP_202_ACCEPTED)


class ScheduleExportRecordViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ScheduleExportRecordSerializer
    queryset = ScheduleExportRecord.objects.filter(is_deleted=False).select_related('version__schedule__project', 'requested_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(version__schedule__project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(version__schedule__project_id=project_id) if project_id else queryset


class PlanningEnterpriseViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'planning_enterprise'

    @action(detail=False, methods=['get'])
    def portfolio(self, request):
        projects = accessible_projects(request.user).select_related('enterprise_project')
        rows = []
        for project in projects:
            schedule = project.schedules.filter(is_deleted=False).first()
            version = schedule.versions.filter(is_deleted=False).first() if schedule else None
            snapshot = version.control_snapshots.filter(is_deleted=False).first() if version else None
            rows.append({
                'id': project.id, 'name': project.name, 'client': project.client, 'phase': project.phase,
                'enterprise_code': project.enterprise_project.code if project.enterprise_project_id else '',
                'schedule_id': schedule.id if schedule else None, 'schedule_status': schedule.status if schedule else None,
                'version_id': version.id if version else None, 'version': version.version if version else None,
                'version_status': version.status if version else None,
                'calculated_finish': version.calculated_finish if version else None,
                'progress_pct': snapshot.progress_pct if snapshot else None,
                'spi': snapshot.spi if snapshot else None, 'cpi': snapshot.cpi if snapshot else None,
                'open_governance_items': version.governance_items.filter(is_deleted=False).exclude(
                    status__in=['closed', 'implemented', 'rejected'],
                ).count() if version else 0,
                'failed_jobs': project.jobs.filter(is_deleted=False, status='failed').count(),
                'failed_deliveries': IntegrationDelivery.objects.filter(
                    endpoint__project=project, is_deleted=False, status='failed',
                ).count(),
            })
        return Response({
            'summary': {
                'project_count': len(rows),
                'at_risk_count': sum(1 for row in rows if (row['spi'] is not None and row['spi'] < 1) or row['failed_deliveries']),
                'pending_governance': sum(row['open_governance_items'] for row in rows),
                'failed_operations': sum(row['failed_jobs'] + row['failed_deliveries'] for row in rows),
            },
            'projects': rows,
        })

    def _project(self, request):
        project = accessible_projects(request.user).filter(pk=request.query_params.get('project') or request.data.get('project')).first()
        return project

    @action(detail=False, methods=['get'])
    def readiness(self, request):
        project = self._project(request)
        if not project:
            return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)
        now = timezone.now()
        last_day = now - dt.timedelta(days=1)
        return Response({
            'project_id': project.id,
            'status': 'degraded' if project.jobs.filter(is_deleted=False, status='failed', created_at__gte=last_day).exists() else 'healthy',
            'integration_encryption_configured': integration_secrets.is_configured(),
            'integrations': {
                'active': project.integration_endpoints.filter(is_deleted=False, is_active=True).count(),
                'failed_deliveries_24h': IntegrationDelivery.objects.filter(
                    endpoint__project=project, is_deleted=False, status='failed', created_at__gte=last_day,
                ).count(),
                'queued_deliveries': IntegrationDelivery.objects.filter(
                    endpoint__project=project, is_deleted=False, status__in=['queued', 'delivering'],
                ).count(),
            },
            'operations': {
                'failed_jobs_24h': project.jobs.filter(is_deleted=False, status='failed', created_at__gte=last_day).count(),
                'running_jobs': project.jobs.filter(is_deleted=False, status__in=['queued', 'running']).count(),
                'exports_30d': ScheduleExportRecord.objects.filter(
                    version__schedule__project=project, is_deleted=False, created_at__gte=now - dt.timedelta(days=30),
                ).count(),
                'audit_events_30d': project.audit_events.filter(created_at__gte=now - dt.timedelta(days=30)).count(),
            },
        })

    @action(detail=False, methods=['get', 'put'])
    def retention(self, request):
        project = self._project(request)
        if not project:
            return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)
        policy, _ = PlanningRetentionPolicy.objects.get_or_create(project=project)
        if request.method == 'GET':
            return Response(PlanningRetentionPolicySerializer(policy).data)
        if not can_write_project(request.user, project):
            return Response({'error': 'You cannot update this retention policy.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = PlanningRetentionPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        record_event(project=project, actor=request.user, action='enterprise.retention_updated', entity=policy)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='retention-cleanup')
    def retention_cleanup(self, request):
        project = self._project(request)
        if not project:
            return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not can_write_project(request.user, project):
            return Response({'error': 'You cannot execute retention for this project.'}, status=status.HTTP_403_FORBIDDEN)
        policy, _ = PlanningRetentionPolicy.objects.get_or_create(project=project)
        if policy.legal_hold:
            return Response({'error': 'Retention cleanup is blocked by legal hold.'}, status=status.HTTP_409_CONFLICT)
        now = timezone.now()
        querysets = {
            'exports': ScheduleExportRecord.objects.filter(
                version__schedule__project=project, is_deleted=False,
                created_at__lt=now - dt.timedelta(days=policy.export_history_days),
            ),
            'deliveries': IntegrationDelivery.objects.filter(
                endpoint__project=project, is_deleted=False,
                created_at__lt=now - dt.timedelta(days=policy.delivery_history_days),
            ),
            'jobs': PlanningJob.objects.filter(
                project=project, is_deleted=False, status__in=['succeeded', 'failed', 'cancelled'],
                created_at__lt=now - dt.timedelta(days=policy.completed_job_days),
            ),
        }
        counts = {name: queryset.count() for name, queryset in querysets.items()}
        execute = bool(request.data.get('execute'))
        if execute:
            if request.data.get('confirmation') != project.name:
                return Response({'error': 'Confirmation must exactly match the project name.'}, status=status.HTTP_400_BAD_REQUEST)
            for queryset in querysets.values():
                queryset.update(is_deleted=True, deleted_at=now)
            record_event(
                project=project, actor=request.user, action='enterprise.retention_executed', entity=policy,
                after=counts,
            )
        return Response({'execute': execute, 'legal_hold': False, 'eligible': counts})
