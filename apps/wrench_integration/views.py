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

from .models import WrenchConfig, WrenchSyncLog, WrenchS3SyncJob
from .serializers import (
    WrenchConfigReadSerializer,
    WrenchConfigWriteSerializer,
    WrenchSyncLogSerializer,
    WrenchS3SyncJobSerializer,
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
            resource_id=None,
            resource_repr=str(cfg),
            metadata={'config_id': cfg.id},
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
            resource_id=None,
            resource_repr=str(cfg),
            metadata={'config_id': cfg.id},
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
            resource_id=None,
            resource_repr='Connection verification',
            metadata={'config_id': cfg.id, 'result': result},
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
            resource_id=None,
            resource_repr=str(log),
            metadata={'log_id': log.id, 'direction': direction, 'entity_type': entity_type, 'status': log.status},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        return Response(WrenchSyncLogSerializer(log).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='search-documents')
    def search_documents(self, request):
        """
        Search Wrench documents.
        Strategy: try REST GetDocumentList first (same host as transmittals, no SVC URL needed),
                  fall back to DocumentSearch/SearchObject (requires SVC URL).

        Request body (all optional):
          discipline  – filter by discipline code
          doc_no      – exact match on DOC_NO
          date_from   – APPROVED_ON >= this date ('YYYY/MM/DD HH:MM')  [DocumentSearch only]
          date_to     – APPROVED_ON <= this date ('YYYY/MM/DD HH:MM')  [DocumentSearch only]
          page        – page number (default 1)
          page_size   – results per page (default 50, max 200)
        """
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Please configure the integration first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page      = int(request.data.get('page', 1))
        page_size = min(int(request.data.get('page_size', 50)), 200)
        discipline = request.data.get('discipline') or None
        doc_no     = request.data.get('doc_no') or None
        date_from  = request.data.get('date_from') or None
        date_to    = request.data.get('date_to') or None

        # ── Strategy 1: REST GetDocumentList (no SVC URL required) ──────────
        try:
            result = wrench_service.get_document_list(
                cfg,
                page=page,
                page_size=page_size,
                discipline=discipline,
                doc_no=doc_no,
            )
            result['source'] = 'rest'
            return Response(result, status=status.HTTP_200_OK)
        except Exception as rest_exc:
            logger.info('[Wrench] REST document list failed (%s), trying DocumentSearch', rest_exc)

        # ── Strategy 2: DocumentSearch/SearchObject (requires SVC URL) ──────
        try:
            result = wrench_service.search_documents(
                cfg,
                page=page,
                page_size=page_size,
                discipline=discipline,
                date_from=date_from,
                date_to=date_to,
                doc_no=doc_no,
            )
            result['source'] = 'document_search'
            return Response(result, status=status.HTTP_200_OK)
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except http_lib.exceptions.ConnectionError:
            return Response({'detail': 'Unable to reach the Wrench server.'}, status=status.HTTP_502_BAD_GATEWAY)
        except http_lib.exceptions.HTTPError as exc:
            return Response({'detail': f'Wrench returned HTTP {exc.response.status_code}.'}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:
            logger.error('[Wrench] Document search failed: %s', exc, exc_info=True)
            return Response({'detail': 'Document search failed. Check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='document-choices')
    def document_choices(self, request):
        """
        Return unique discipline codes and document numbers drawn from a sample search.
        Used to populate dropdowns in the Document Search UI.

        GET /api/v1/wrench/sync/document-choices/
        Response: { disciplines: [...], doc_numbers: [...] }
        """
        # Soft-coded sample size — large enough to cover most project disciplines
        _CHOICES_SAMPLE_SIZE = 200

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Strategy 1: Try the REST GetDocumentList endpoint (same host as transmittals).
        # Strategy 2: Fall back to DocumentSearch/SearchObject (needs SVC URL).
        # Whichever succeeds, extract unique disciplines + doc numbers.
        result = None
        svc_url_required = False

        try:
            result = wrench_service.get_document_list(cfg, page=1, page_size=_CHOICES_SAMPLE_SIZE)
            logger.info('[Wrench] document-choices: loaded %d docs via REST', result['total'])
        except Exception as rest_exc:
            logger.warning('[Wrench] document-choices REST failed (%s), trying DocumentSearch', rest_exc)
            try:
                result = wrench_service.search_documents(cfg, page=1, page_size=_CHOICES_SAMPLE_SIZE)
            except RuntimeError as exc:
                err_msg = str(exc)
                svc_url_required = 'DocumentSearch endpoint not found' in err_msg
                logger.warning('[Wrench] document-choices DocumentSearch also failed: %s', err_msg)
            except Exception as exc:
                logger.warning('[Wrench] document-choices unexpected error: %s', exc)

        if result is None:
            return Response(
                {'disciplines': [], 'doc_numbers': [], 'svc_url_required': svc_url_required},
                status=status.HTTP_200_OK,
            )

        disciplines = sorted({
            doc.get('DISCIPLINE', '').strip()
            for doc in result.get('documents', [])
            if doc.get('DISCIPLINE', '').strip()
        })
        doc_numbers = sorted({
            doc.get('DOC_NO', '').strip()
            for doc in result.get('documents', [])
            if doc.get('DOC_NO', '').strip()
        })

        return Response(
            {'disciplines': disciplines, 'doc_numbers': doc_numbers, 'svc_url_required': False},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='list-transmittals')
    def list_transmittals(self, request):
        """
        List transmittals from Wrench via the SmartProject REST WebAPI.
        GET /api/v1/wrench/sync/list-transmittals/?page=1&page_size=50
        """
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Please configure the integration first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', 50)), 500)

        try:
            result = wrench_service.get_transmittals(cfg, page=page, page_size=page_size)
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
            logger.error('[Wrench] List transmittals failed: %s', exc, exc_info=True)
            return Response(
                {'detail': 'Failed to list transmittals. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='trans-documents')
    def trans_documents(self, request):
        """
        Return documents linked to a specific transmittal.

        Strategy (transparent to frontend — first success wins):
          1. Transmittal-specific REST endpoints  (no SVC URL required)
          2. Generic Document REST GetDocumentList (no SVC URL required)
          3. DocumentSearch/SearchObject fallback  (uses SVC URL if configured)

        GET /api/v1/wrench/sync/trans-documents/?order_no=<ORDER_NO>&trans_id=<TRANS_ID>
        Response: { total, documents: [{DOC_NO, DOC_DESCRIPTION, ...}], source }
        """
        # Soft-coded default page size for per-transmittal document fetch
        _TRANS_DOC_DEFAULT_PAGE_SIZE = 200

        order_no = request.query_params.get('order_no', '').strip()
        trans_id = request.query_params.get('trans_id', '').strip() or None

        if not order_no:
            return Response(
                {'detail': 'order_no query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page      = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', _TRANS_DOC_DEFAULT_PAGE_SIZE)), 500)

        try:
            result = wrench_service.get_transmittal_documents(
                cfg,
                order_no=order_no,
                trans_id=trans_id,
                page=page,
                page_size=page_size,
            )
            result['svc_url_required'] = False
            return Response(result, status=status.HTTP_200_OK)
        except RuntimeError as exc:
            logger.warning('[Wrench] trans_documents: all strategies failed for order_no=%s: %s', order_no, exc)
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as exc:
            logger.error('[Wrench] trans_documents unexpected error for order_no=%s: %s', order_no, exc, exc_info=True)
            return Response(
                {'detail': 'Could not load documents for this transmittal. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class WrenchS3SyncViewSet(viewsets.ViewSet):
    """
    Wrench → RADAI → AWS S3 export jobs.

    GET  /api/v1/wrench/s3-sync/             – list recent jobs
    POST /api/v1/wrench/s3-sync/start/       – start a batch or real-time job
    GET  /api/v1/wrench/s3-sync/<id>/        – retrieve job detail
    POST /api/v1/wrench/s3-sync/<id>/stop/   – stop a real-time job
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    # Soft-coded: allowed values validated here so the frontend can rely on them
    _VALID_MODES    = [WrenchS3SyncJob.MODE_BATCH, WrenchS3SyncJob.MODE_REALTIME]
    _VALID_ENTITIES = [
        WrenchS3SyncJob.ENTITY_TRANSMITTALS,
        WrenchS3SyncJob.ENTITY_DOCUMENTS,
        WrenchS3SyncJob.ENTITY_ALL,
    ]
    _DEFAULT_S3_PREFIX = 'wrench/'

    def list(self, request):
        jobs = WrenchS3SyncJob.objects.select_related('triggered_by').order_by('-started_at')[:50]
        return Response(WrenchS3SyncJobSerializer(jobs, many=True).data)

    def retrieve(self, request, pk=None):
        try:
            job = WrenchS3SyncJob.objects.get(pk=pk)
        except WrenchS3SyncJob.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(WrenchS3SyncJobSerializer(job).data)

    @action(detail=False, methods=['post'], url_path='start')
    def start(self, request):
        """
        Start an S3 export job.

        Request body:
          mode        – 'batch' | 'realtime'   (default: 'batch')
          entity_type – 'transmittals' | 'documents' | 'all'  (default: 'transmittals')
          s3_prefix   – optional S3 key prefix  (default: 'wrench/')
        """
        from .tasks import wrench_s3_batch_export, wrench_s3_realtime_tick

        mode        = request.data.get('mode', WrenchS3SyncJob.MODE_BATCH)
        entity_type = request.data.get('entity_type', WrenchS3SyncJob.ENTITY_TRANSMITTALS)
        s3_prefix   = request.data.get('s3_prefix', self._DEFAULT_S3_PREFIX) or self._DEFAULT_S3_PREFIX

        if mode not in self._VALID_MODES:
            return Response(
                {'detail': f'Invalid mode. Choose from {self._VALID_MODES}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if entity_type not in self._VALID_ENTITIES:
            return Response(
                {'detail': f'Invalid entity_type. Choose from {self._VALID_ENTITIES}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Configure the integration first.'},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        # Prevent duplicate in-progress real-time jobs
        if mode == WrenchS3SyncJob.MODE_REALTIME:
            running = WrenchS3SyncJob.objects.filter(
                mode=WrenchS3SyncJob.MODE_REALTIME,
                status=WrenchS3SyncJob.STATUS_IN_PROGRESS,
            ).first()
            if running:
                return Response(
                    {'detail': f'A real-time job (id={running.id}) is already running. Stop it first.'},
                    status=status.HTTP_409_CONFLICT,
                )

        job = WrenchS3SyncJob.objects.create(
            config=cfg,
            triggered_by=request.user,
            mode=mode,
            entity_type=entity_type,
            s3_prefix=s3_prefix,
            status=WrenchS3SyncJob.STATUS_PENDING,
        )

        # Dispatch async — never block the request
        if mode == WrenchS3SyncJob.MODE_BATCH:
            task = wrench_s3_batch_export.apply_async(args=[job.id])
        else:
            task = wrench_s3_realtime_tick.apply_async(args=[job.id])

        job.celery_task_id = task.id
        job.save(update_fields=['celery_task_id', 'updated_at'])

        create_audit_log(
            user=request.user,
            action='execute',
            resource_type='WrenchS3SyncJob',
            resource_id=None,
            resource_repr=str(job),
            metadata={'job_id': job.id, 'mode': mode, 'entity_type': entity_type},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        logger.info('[S3 View] Dispatched %s job id=%d task=%s', mode, job.id, task.id)
        return Response(WrenchS3SyncJobSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='stop')
    def stop(self, request, pk=None):
        """Stop a running real-time job."""
        from .s3_service import stop_realtime_job

        try:
            job = WrenchS3SyncJob.objects.get(pk=pk)
        except WrenchS3SyncJob.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if job.status not in (WrenchS3SyncJob.STATUS_IN_PROGRESS, WrenchS3SyncJob.STATUS_PENDING):
            return Response(
                {'detail': f'Job is not running (status={job.status}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stop_realtime_job(job)
        create_audit_log(
            user=request.user,
            action='update',
            resource_type='WrenchS3SyncJob',
            resource_id=None,
            resource_repr=f'Stop job {pk}',
            metadata={'job_id': job.id},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return Response(WrenchS3SyncJobSerializer(job).data)
