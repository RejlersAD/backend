import hashlib
import logging
import mimetypes
import re
import secrets
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Max, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.core.project_models import Project
from apps.project_control.access import can_write_enterprise_project
from .models import ReplicaEntry, ReplicaExtraction, ReplicaScan, ReplicaScope, ReplicaSource, ReplicaVersion
from .paths import config_hash, included, included_entries_query, normalize_path, path_key
from .permissions import ReplicaAdminPermission, action_allowed, is_replica_admin, visible_entries, visible_scopes
from .serializers import (EntrySerializer, ExtractionSerializer, InventorySerializer, ScopeSerializer,
                          SourceSerializer, VersionSerializer)

logger = logging.getLogger(__name__)


class ReplicaPagination(PageNumberPagination):
    page_size = 100


def valid_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('A valid record ID is required.')


def valid_project_id(value):
    try:
        number = int(value)
        if number < 1:
            raise ValueError
        return number
    except (ValueError, TypeError):
        raise ValidationError('A valid project ID is required.')


def require_action(user, action_name):
    if not action_allowed(user, action_name):
        raise PermissionDenied(f'Project Control {action_name} permission is required.')


class SourceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, ReplicaAdminPermission]
    serializer_class = SourceSerializer
    queryset = ReplicaSource.objects.all()
    pagination_class = ReplicaPagination

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            source = get_object_or_404(self.get_queryset().select_for_update(), pk=kwargs['pk'])
            self.check_object_permissions(request, source)
            before = config_hash(source)
            serializer = self.get_serializer(source, data=request.data, partial=kwargs.get('partial', False))
            serializer.is_valid(raise_exception=True)
            serializer.save()
            if source.active_run and before != config_hash(source):
                ReplicaScan.objects.filter(pk=source.active_run, status='running').update(
                    status='failed', error='Source configuration changed; start a new scan.', completed_at=timezone.now(),
                )
                source.active_run = None
                source.last_error = 'Source configuration changed; waiting for a new scan.'
                source.save(update_fields=['active_run', 'last_error'])
        return Response(self.get_serializer(source).data)

    @action(detail=True, methods=['post'], url_path='rotate-token')
    def rotate_token(self, request, pk=None):
        token = secrets.token_urlsafe(48)
        with transaction.atomic():
            source = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            self.check_object_permissions(request, source)
            source.token_hash = hashlib.sha256(token.encode()).hexdigest()
            source.save(update_fields=['token_hash'])
        response = Response({'token': token})
        response['Cache-Control'] = 'no-store'
        return response

    @action(detail=True, methods=['get'])
    def scans(self, request, pk=None):
        source = self.get_object()
        return Response(list(source.scans.values('id', 'status', 'started_at', 'completed_at', 'error')[:30]))


class ScopeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ScopeSerializer
    pagination_class = ReplicaPagination
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        require_action(self.request.user, 'read')
        qs = visible_scopes(self.request.user)
        for key in ['source', 'project']:
            if self.request.query_params.get(key):
                validator = valid_project_id if key == 'project' else valid_uuid
                qs = qs.filter(**{key + '_id': validator(self.request.query_params[key])})
        return qs

    def perform_update(self, serializer):
        if not is_replica_admin(self.request.user):
            raise PermissionDenied('Only a replica administrator can publish folder access or change project mappings.')
        serializer.save()


class EntryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EntrySerializer
    pagination_class = ReplicaPagination

    def get_queryset(self):
        require_action(self.request.user, 'read')
        qs = visible_entries(self.request.user)
        for key in ['source', 'scope']:
            if self.request.query_params.get(key):
                qs = qs.filter(**{key + '_id': valid_uuid(self.request.query_params[key])})
        if self.request.query_params.get('project'):
            qs = qs.filter(scope__project_id=valid_project_id(self.request.query_params['project']))
        search = self.request.query_params.get('search', '').strip()[:200]
        if search:
            # Only current extracted content contributes to search results.
            qs = qs.filter(Q(name__icontains=search) | Q(relative_path__icontains=search) |
                           Q(extractions__version_id=F('current_version_id'), extractions__sections__icontains=search,
                             status='available')).distinct()
        elif 'parent_path' in self.request.query_params and self.action == 'list':
            qs = qs.filter(parent_path__iexact=normalize_path(self.request.query_params['parent_path'], allow_empty=True))
        return qs

    @action(detail=True, methods=['get'])
    def versions(self, request, pk=None):
        return Response(VersionSerializer(self.get_object().versions.all()[:100], many=True).data)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        require_action(request.user, 'export')
        entry = self.get_object()
        if entry.is_directory:
            raise ValidationError('Select a file to download.')
        if request.query_params.get('version'):
            version = get_object_or_404(entry.versions, pk=valid_uuid(request.query_params['version']))
        else:
            version = entry.current_version
            if entry.status not in ('available', 'missing'):
                return Response({'detail': 'The current file has not finished synchronizing.'}, status=409)
        if not version:
            return Response({'detail': 'This file is indexed only; enable mirror mode and synchronize its folder.'}, status=409)
        safe_inline = entry.content_type in {'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'text/plain'}
        inline = request.query_params.get('inline') == '1' and safe_inline
        try:
            response = FileResponse(version.file.open('rb'), as_attachment=not inline, filename=entry.name,
                                    content_type=entry.content_type if safe_inline else 'application/octet-stream')
        except (OSError, ValueError):
            return Response({'detail': 'Replica storage is temporarily unavailable.'}, status=503)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return response

    @action(detail=True, methods=['get'])
    def extractions(self, request, pk=None):
        rows = self.get_object().extractions.select_related('entry', 'version').all()[:50]
        return Response(ExtractionSerializer(rows, many=True).data)

    @action(detail=True, methods=['post'])
    def extract(self, request, pk=None):
        require_action(request.user, 'create')
        entry = self.get_object()
        if not is_replica_admin(request.user) and not can_write_enterprise_project(request.user, entry.scope.project):
            raise PermissionDenied('Project write access is required to extract information.')
        if entry.is_directory or not entry.current_version or entry.status != 'available':
            return Response({'detail': 'Synchronize the current file before extracting information.'}, status=409)
        from .extraction import extract_content
        result = ReplicaExtraction(entry=entry, version=entry.current_version, requested_by=request.user)
        try:
            with entry.current_version.file.open('rb') as content:
                payload = extract_content(content, entry.name)
            result.sections = payload['sections']
            result.suggestions = payload['suggestions']
            result.warnings = payload.get('warnings', [])
        except ValueError as exc:
            result.status = 'failed'
            result.error = str(exc)[:2000]
        except Exception:
            logger.exception('Replica extraction failed for entry %s', entry.id)
            result.status = 'failed'
            result.error = 'The document could not be read. Check its format and storage availability.'
        result.save()
        entry.refresh_from_db()
        result.entry = entry
        return Response(ExtractionSerializer(result).data, status=201)


class ExtractionViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ExtractionSerializer

    def get_queryset(self):
        require_action(self.request.user, 'read')
        return ReplicaExtraction.objects.filter(entry__in=visible_entries(self.request.user)).select_related('entry__scope__project', 'version')

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        require_action(request.user, 'update')
        extraction = self.get_object()
        entry = extraction.entry
        if not is_replica_admin(request.user) and not can_write_enterprise_project(request.user, entry.scope.project):
            raise PermissionDenied('Project write access is required to review extracted information.')
        review_status = request.data.get('status')
        notes = request.data.get('notes', '')
        if review_status not in ('accepted', 'rejected') or not isinstance(notes, str) or len(notes) > 5000:
            raise ValidationError('Choose accepted or rejected and provide at most 5000 characters of notes.')
        with transaction.atomic():
            entry = ReplicaEntry.objects.select_for_update().get(pk=entry.pk)
            extraction = ReplicaExtraction.objects.select_for_update().get(pk=extraction.pk)
            if extraction.status != 'pending_review':
                return Response({'detail': 'This extraction has already been reviewed or failed.'}, status=409)
            if review_status == 'accepted' and (entry.current_version_id != extraction.version_id or entry.status != 'available'):
                return Response({'detail': 'The source changed. Extract and review its current version.'}, status=409)
            extraction.status = review_status
            extraction.review_notes = notes
            extraction.reviewed_by = request.user
            extraction.reviewed_at = timezone.now()
            extraction.save(update_fields=['status', 'review_notes', 'reviewed_by', 'reviewed_at'])
        return Response(ExtractionSerializer(extraction).data)


def agent_source(request, allow_disabled=False, locked=False):
    source_id = request.headers.get('X-Replica-Source', '')
    authorization = request.headers.get('Authorization', '')
    try:
        queryset = ReplicaSource.objects.select_for_update() if locked else ReplicaSource.objects.all()
        source = queryset.get(pk=uuid.UUID(source_id))
    except (ValueError, ReplicaSource.DoesNotExist):
        raise AuthenticationFailed('Invalid connector credentials.')
    scheme, _, token = authorization.partition(' ')
    digest = hashlib.sha256(token.encode()).hexdigest()
    if scheme.lower() != 'bearer' or not token or not source.token_hash or not constant_time_compare(digest, source.token_hash):
        raise AuthenticationFailed('Invalid connector credentials.')
    if not source.enabled and not allow_disabled:
        raise PermissionDenied('This replica source is disabled.')
    return source


def active_scan(source, scan_id):
    scan = get_object_or_404(ReplicaScan.objects.select_for_update(), pk=valid_uuid(scan_id), source=source)
    if scan.status != 'running' or source.active_run != scan.id:
        raise ValidationError('This scan is no longer active. Start a new scan.')
    if scan.config_hash != config_hash(source):
        raise ValidationError('Source configuration changed. Start a new scan with the current configuration.')
    return scan


def touch(source, scan=None):
    now = timezone.now()
    ReplicaSource.objects.filter(pk=source.pk).update(last_heartbeat=now)
    if scan:
        ReplicaScan.objects.filter(pk=scan.pk).update(updated_at=now)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_config(request):
    source = agent_source(request, allow_disabled=True)
    touch(source)
    response = Response({key: getattr(source, key) for key in (
        'id', 'name', 'root_path', 'included_paths', 'excluded_paths', 'mode', 'max_file_size_mb', 'interval_seconds', 'enabled',
    )})
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_start(request):
    source = agent_source(request)
    run_id = valid_uuid(request.data.get('run_id'))
    with transaction.atomic():
        source = agent_source(request, locked=True)
        existing = ReplicaScan.objects.filter(pk=run_id).first()
        if existing:
            if existing.source_id != source.id:
                raise ValidationError('Run ID is already in use.')
            return Response({'id': existing.id, 'status': existing.status})
        if source.active_run:
            previous = ReplicaScan.objects.filter(pk=source.active_run, status='running').first()
            if previous and previous.updated_at > timezone.now() - timedelta(minutes=30):
                return Response({'detail': 'Another scan is active for this source.'}, status=409)
            if previous:
                previous.status, previous.error, previous.completed_at = 'failed', 'Connector stopped before completion.', timezone.now()
                previous.save(update_fields=['status', 'error', 'completed_at'])
        scan = ReplicaScan.objects.create(id=run_id, source=source, config_hash=config_hash(source))
        source.active_run, source.last_heartbeat, source.last_error = scan.id, timezone.now(), ''
        source.save(update_fields=['active_run', 'last_heartbeat', 'last_error'])
    return Response({'id': scan.id, 'status': scan.status}, status=201)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_heartbeat(request, scan_id):
    """Keep a real running scan online while a slow share read is in progress."""
    with transaction.atomic():
        source = agent_source(request, locked=True)
        scan = active_scan(source, scan_id)
        touch(source, scan)
    return Response({'id': scan.id, 'status': scan.status})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_inventory(request, scan_id):
    source = agent_source(request)
    rows = request.data.get('entries')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValidationError('Send between 1 and 200 entries per batch.')
    serializer = InventorySerializer(data=rows, many=True)
    serializer.is_valid(raise_exception=True)
    output = []
    with transaction.atomic():
        source = agent_source(request, locked=True)
        scan = active_scan(source, scan_id)
        records = serializer.validated_data
        keys = [path_key(data['relative_path']) for data in records]
        if len(set(keys)) != len(keys):
            raise ValidationError('Send each source path only once in a batch.')
        existing = {entry.path_key: entry for entry in source.entries.filter(path_key__in=keys).select_related('current_version')}
        scopes = {}
        create_entries, update_entries = [], []
        batch_time = timezone.now()
        previous_error = scan.error
        for data in records:
            path = data['relative_path']
            if not included(source, path) or (not source.included_paths and not data['is_directory']):
                raise ValidationError('An entry is outside the configured folder scope.')
            folder = path.split('/')[0]
            folder_key = path_key(folder)
            if folder_key not in scopes:
                scope, created = ReplicaScope.objects.get_or_create(source=source, path_key=folder_key, defaults={'relative_path': folder})
                if created:
                    match = re.match(r'^(\d+)(?:[\s_-]|$)', folder)
                    if match:
                        scope.project = Project.objects.filter(code=match.group(1), is_deleted=False).first()
                        scope.save(update_fields=['project'])
                scopes[folder_key] = scope
            scope = scopes[folder_key]
            entry_key = path_key(path)
            entry = existing.get(entry_key)
            if entry is None:
                entry = ReplicaEntry(source=source, scope=scope, path_key=entry_key)
                create_entries.append(entry)
            else:
                update_entries.append(entry)
            previous_version = entry.current_version
            for key, value in data.items():
                setattr(entry, key, value)
            entry.normalized_path = path.casefold()
            entry.content_type = 'inode/directory' if entry.is_directory else mimetypes.guess_type(entry.name)[0] or 'application/octet-stream'
            entry.last_seen_scan, entry.last_seen_at = scan, batch_time
            same_content = bool(previous_version and not entry.is_directory and (
                (entry.checksum and previous_version.checksum == entry.checksum) or
                (not entry.checksum and previous_version.size_bytes == entry.size_bytes and previous_version.modified_at == entry.modified_at)
            ))
            if entry.error:
                entry.status = 'failed'
                scan.error = 'One or more source entries could not be read.'
            elif entry.is_directory:
                entry.status = 'indexed'
            elif same_content:
                entry.status = 'available'
            elif source.mode == 'mirror':
                entry.status = 'pending'
            else:
                entry.status = 'indexed'
            upload_required = entry.status == 'pending'
            if upload_required and (not entry.checksum or entry.size_bytes > source.max_file_size_mb * 1024 * 1024):
                entry.status, entry.error = 'failed', 'Missing checksum or file exceeds the configured size limit.'
                scan.error = 'One or more files could not be synchronized.'
                upload_required = False
            output.append({'id': entry.id, 'relative_path': path, 'upload_required': upload_required, 'status': entry.status})
        ReplicaEntry.objects.bulk_create(create_entries, batch_size=200)
        ReplicaEntry.objects.bulk_update(update_entries, [
            'relative_path', 'normalized_path', 'parent_path', 'name', 'is_directory', 'size_bytes',
            'modified_at', 'checksum', 'content_type', 'error', 'status', 'last_seen_scan', 'last_seen_at',
        ], batch_size=200)
        if scan.error != previous_error:
            scan.save(update_fields=['error'])
        touch(source, scan)
    return Response({'entries': output})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_content(request, entry_id):
    source = agent_source(request)
    content = request.FILES.get('file')
    checksum = request.data.get('checksum', '')
    if not content or not isinstance(checksum, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', checksum):
        raise ValidationError('Provide a file and its SHA-256 checksum.')
    try:
        modified_at = parse_datetime(request.data.get('modified_at', ''))
    except (ValueError, TypeError):
        modified_at = None
    if modified_at is None or timezone.is_naive(modified_at):
        raise ValidationError('Provide the inventory modification time with its timezone.')
    if content.size > source.max_file_size_mb * 1024 * 1024:
        raise ValidationError('The file exceeds the configured size limit.')
    digest = hashlib.sha256()
    for chunk in content.chunks():
        digest.update(chunk)
    if digest.hexdigest() != checksum.lower():
        raise ValidationError('File checksum does not match the uploaded contents.')
    content.seek(0)
    with transaction.atomic():
        source = agent_source(request, locked=True)
        scan = active_scan(source, request.data.get('scan_id'))
        entry = get_object_or_404(ReplicaEntry.objects.select_for_update(), pk=entry_id, source=source, last_seen_scan=scan)
        if not included(source, entry.relative_path) or source.mode != 'mirror' or entry.is_directory:
            raise PermissionDenied('File content is outside this source replication scope.')
        if entry.checksum != checksum.lower() or entry.size_bytes != content.size or entry.modified_at != modified_at:
            raise ValidationError('File changed after inventory. Rescan it before uploading.')
        if entry.current_version and entry.current_version.checksum == checksum.lower():
            entry.status, entry.error = 'available', ''
            entry.save(update_fields=['status', 'error'])
            touch(source, scan)
            return Response({'id': entry.id, 'version': entry.current_version_id, 'status': 'available'})
        number = (entry.versions.aggregate(last=Max('number'))['last'] or 0) + 1
        version = ReplicaVersion(entry=entry, number=number, checksum=checksum.lower(), size_bytes=content.size, modified_at=entry.modified_at)
        version.file.save('content', content, save=False)
        try:
            version.save()
            entry.current_version, entry.status, entry.error = version, 'available', ''
            entry.save(update_fields=['current_version', 'status', 'error'])
        except Exception:
            version.file.delete(save=False)
            raise
        touch(source, scan)
    return Response({'id': entry.id, 'version': version.id, 'status': 'available'}, status=201)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_complete(request, scan_id):
    source = agent_source(request)
    if not isinstance(request.data.get('success'), bool):
        raise ValidationError('Provide an explicit boolean scan success value.')
    with transaction.atomic():
        source = agent_source(request, locked=True)
        scan = get_object_or_404(ReplicaScan.objects.select_for_update(), pk=scan_id, source=source)
        if scan.status != 'running':
            return Response({'id': scan.id, 'status': scan.status})
        if source.active_run != scan.id:
            raise ValidationError('This scan is no longer active.')
        complete = request.data['success'] and not scan.error and scan.config_hash == config_hash(source)
        if source.entries.filter(last_seen_scan=scan, status__in=['pending', 'failed']).exists():
            complete = False
        now = timezone.now()
        scan.status = 'completed' if complete else 'failed'
        scan.completed_at = now
        scan.error = '' if complete else str(request.data.get('error') or scan.error or 'Scan incomplete or configuration changed.')[:2000]
        scan.save(update_fields=['status', 'completed_at', 'error'])
        if complete:
            # Only entries within THIS scan's configured scope can become missing.
            candidates = source.entries.exclude(last_seen_scan=scan).exclude(status='missing')
            candidates.filter(included_entries_query(source)).update(status='missing')
            source.last_success_at = now
        source.active_run, source.last_heartbeat, source.last_error = None, now, scan.error
        source.save(update_fields=['active_run', 'last_heartbeat', 'last_success_at', 'last_error'])
    return Response({'id': scan.id, 'status': scan.status})
