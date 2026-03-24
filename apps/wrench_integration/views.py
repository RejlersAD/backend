"""
Wrench Integration – API Views
All endpoints require IsAdmin permission (Admin or Super Admin only).
"""
import logging
import requests as http_lib
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

from apps.rbac.permissions import IsAdmin, IsSuperAdmin
from apps.rbac.utils import create_audit_log

from .models import WrenchConfig, WrenchSyncLog
from .serializers import (
    WrenchConfigReadSerializer,
    WrenchConfigWriteSerializer,
    WrenchSyncLogSerializer,
)
from . import service as wrench_service

logger = logging.getLogger(__name__)


class WrenchConfigViewSet(viewsets.ViewSet):
    """
    Manage the Wrench platform connection configuration.

    GET  /api/v1/wrench/config/           – retrieve active config (safe, no key)
    POST /api/v1/wrench/config/           – create / update config
    POST /api/v1/wrench/config/verify/    – test connection
    DELETE /api/v1/wrench/config/<id>/    – remove config (super admin only)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def list(self, request):
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response({'configured': False, 'config': None})
        serializer = WrenchConfigReadSerializer(cfg)
        return Response({'configured': True, 'config': serializer.data})

    def create(self, request):
        """Create or replace the active Wrench config."""
        serializer = WrenchConfigWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Deactivate any existing configs before creating (soft singleton)
        WrenchConfig.objects.filter(is_active=True).update(is_active=False)

        cfg = serializer.save(
            created_by=request.user,
            updated_by=request.user,
        )
        create_audit_log(
            user=request.user,
            action='create',
            resource_type='WrenchConfig',
            resource_id=str(cfg.id),
            resource_repr=str(cfg),
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        read_serializer = WrenchConfigReadSerializer(cfg)
        return Response(
            {'message': 'Wrench configuration saved.', 'config': read_serializer.data},
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        """Only super admins can delete the config."""
        if not (request.user.is_superuser or
                request.user.rbac_profile.roles.filter(code='super_admin', is_active=True).exists()):
            return Response({'detail': 'Super admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            cfg = WrenchConfig.objects.get(pk=pk)
        except WrenchConfig.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        create_audit_log(
            user=request.user,
            action='delete',
            resource_type='WrenchConfig',
            resource_id=str(cfg.id),
            resource_repr=str(cfg),
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        cfg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='verify')
    def verify(self, request):
        """Test the connection to Wrench without storing anything."""
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'success': False, 'message': 'No active configuration found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        result = wrench_service.verify_connection(cfg)
        create_audit_log(
            user=request.user,
            action='read',
            resource_type='WrenchConfig',
            resource_id=str(cfg.id),
            resource_repr='Connection verification',
            metadata={'result': result},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        http_status = status.HTTP_200_OK if result['success'] else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=http_status)


class WrenchSyncViewSet(viewsets.ViewSet):
    """
    Trigger and view synchronisation between RADAI and Wrench.

    GET  /api/v1/wrench/sync/            – list recent sync logs
    POST /api/v1/wrench/sync/trigger/    – start a sync
    GET  /api/v1/wrench/sync/<id>/       – retrieve a specific log
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def list(self, request):
        logs = WrenchSyncLog.objects.select_related('triggered_by').order_by('-started_at')[:50]
        serializer = WrenchSyncLogSerializer(logs, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        try:
            log = WrenchSyncLog.objects.get(pk=pk)
        except WrenchSyncLog.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(WrenchSyncLogSerializer(log).data)

    @action(detail=False, methods=['post'], url_path='trigger')
    def trigger(self, request):
        """Kick off a synchronisation run."""
        direction = request.data.get('direction', 'wrench_to_radai')
        entity_type = request.data.get('entity_type', 'all')

        valid_directions = ['radai_to_wrench', 'wrench_to_radai']
        valid_entities = ['project', 'document', 'transmittal', 'user', 'all']

        if direction not in valid_directions:
            return Response(
                {'detail': f'Invalid direction. Choose from {valid_directions}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if entity_type not in valid_entities:
            return Response(
                {'detail': f'Invalid entity_type. Choose from {valid_entities}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            log = wrench_service.run_sync(
                direction=direction,
                entity_type=entity_type,
                triggered_by=request.user,
            )
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except Exception as exc:
            logger.error('Sync trigger failed: %s', exc, exc_info=True)
            return Response(
                {'detail': 'Sync failed. Check server logs for details.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        create_audit_log(
            user=request.user,
            action='execute',
            resource_type='WrenchSync',
            resource_id=str(log.id),
            resource_repr=str(log),
            metadata={'direction': direction, 'entity_type': entity_type, 'status': log.status},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        return Response(WrenchSyncLogSerializer(log).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='search-documents')
    def search_documents(self, request):
        """
        Search Wrench documents via the SmartProject SearchObject API.

        Request body (all optional):
          discipline  – filter by discipline code
          doc_type    – filter by document type
          doc_no      – exact match on DOC_NO
          date_from   – APPROVED_ON >= this date ('YYYY/MM/DD HH:MM')
          date_to     – APPROVED_ON <= this date ('YYYY/MM/DD HH:MM')
          page        – page number (default 1)
          page_size   – results per page (default 50, max 200)
        """
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Please configure the integration first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page = int(request.data.get('page', 1))
        page_size = min(int(request.data.get('page_size', 50)), 200)  # hard cap

        try:
            result = wrench_service.search_documents(
                cfg,
                page=page,
                page_size=page_size,
                discipline=request.data.get('discipline') or None,
                doc_type=request.data.get('doc_type') or None,
                date_from=request.data.get('date_from') or None,
                date_to=request.data.get('date_to') or None,
                doc_no=request.data.get('doc_no') or None,
            )
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except http_lib.exceptions.ConnectionError:
            return Response(
                {'detail': 'Unable to reach the Wrench server.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except http_lib.exceptions.HTTPError as exc:
            return Response(
                {'detail': f'Wrench returned HTTP {exc.response.status_code}.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            logger.error('[Wrench] Document search failed: %s', exc, exc_info=True)
            return Response(
                {'detail': 'Document search failed. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(result, status=status.HTTP_200_OK)

