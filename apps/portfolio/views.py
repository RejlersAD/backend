"""Portfolio reporting and reviewed uploads under effective module grants."""
import hashlib
import logging
from pathlib import Path

from django.core import signing
from django.db import DatabaseError, transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasModuleAccess
from apps.rbac.action_policy import module_action_allowed
from .access import CanUploadPortfolioWorkbook
from .importer import PortfolioSnapshotChanged, import_workbook
from .models import PortfolioSource
from .reporting import build_workbook_report
from .workbook import MAX_BYTES, PARSER_VERSION

logger = logging.getLogger(__name__)
PREVIEW_SALT = 'portfolio.workbook-upload.v1'
PREVIEW_MAX_AGE = 15 * 60


class WorkbookFilters(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)
    pm = serializers.CharField(required=False, allow_blank=True, max_length=128)
    business_unit = serializers.CharField(required=False, allow_blank=True, max_length=128)
    client = serializers.CharField(required=False, allow_blank=True, max_length=256)
    limit = serializers.IntegerField(required=False, default=50, min_value=1, max_value=200)
    offset = serializers.IntegerField(required=False, default=0, min_value=0, max_value=100000)


class PortfolioWorkbookView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'executive_dashboard'
    permission_action = 'read'
    http_method_names = ['get', 'head', 'options']

    def get(self, request):
        filters = WorkbookFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        response = Response(build_workbook_report(request.user, **filters.validated_data))
        response['Cache-Control'] = 'private, no-store'
        return response


class PortfolioRevenueView(PortfolioWorkbookView):
    """Current uploaded revenue report, aggregated over the authorised filters."""

    def get(self, request):
        from .executive import build_revenue_dashboard

        filters = WorkbookFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        response = Response(build_revenue_dashboard(request.user, **filters.validated_data))
        response['Cache-Control'] = 'private, no-store'
        return response


class OutgoingInvoiceFilters(WorkbookFilters):
    snapshot_id = serializers.IntegerField(required=False, min_value=1)


class PortfolioOutgoingInvoicesView(PortfolioWorkbookView):
    """Live, read-only outgoing invoices for the authorized workbook project scope."""

    def get(self, request):
        from .recorded_invoices import build_recorded_invoices
        from .scope import workbook_scope

        if not all(module_action_allowed(request.user, module, 'read')
                   for module in ('project_control', 'finance_outgoing')):
            raise PermissionDenied('Project Control and Outgoing Invoices read access are required.')
        filters = OutgoingInvoiceFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        values = dict(filters.validated_data)
        expected_snapshot = values.pop('snapshot_id', None)
        limit, offset = values.pop('limit', 50), values.pop('offset', 0)
        try:
            with transaction.atomic():
                source = PortfolioSource.objects.select_related('active_snapshot').filter(key='poc').first()
                if source is None or source.active_snapshot is None:
                    return _private_response({'status': 'unavailable', 'source_snapshot_id': None,
                                              'description': 'Import a portfolio workbook to connect recorded invoices.',
                                              'rows': [], 'project_groups': [], 'totals_by_currency': [], 'coverage': {}})
                snapshot = source.active_snapshot
                if expected_snapshot is not None and expected_snapshot != snapshot.pk:
                    return _private_response({'status': 'error', 'code': 'source_changed',
                                              'detail': 'Portfolio source changed. Refresh the portfolio to reload connected invoices.'}, status=409)
                scope = workbook_scope(snapshot, request.user, **values)
                report = build_recorded_invoices(request.user, scope['rows'], full_source=scope['full_source'],
                                                 limit=limit, offset=offset)
                report.update(source_snapshot_id=snapshot.pk, workbook_reporting_date=snapshot.reporting_date.isoformat())
                return _private_response(report, status=503 if report.get('status') == 'error' else 200)
        except Exception:
            logger.exception('Recorded portfolio invoices unavailable')
            return _private_response({'status': 'error', 'detail': 'Connected outgoing invoices could not be loaded. Try again shortly.',
                                      'rows': [], 'project_groups': [], 'totals_by_currency': [], 'coverage': {}}, status=503)


def _upload(request):
    upload = request.FILES.get('file')
    if upload is None or len(request.FILES) != 1 or len(request.FILES.getlist('file')) != 1:
        raise ValidationError({'file': 'Select one Excel workbook.'})
    filename = Path(str(upload.name).replace('\\', '/')).name
    if not filename.lower().endswith('.xlsx'):
        raise ValidationError({'file': 'Select an .xlsx workbook.'})
    if len(filename) > 255:
        raise ValidationError({'file': 'The workbook filename is too long.'})
    if upload.size > MAX_BYTES:
        raise ValidationError({'file': 'The workbook must be 25 MB or smaller.'})
    content = upload.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise ValidationError({'file': 'The workbook is empty or exceeds 25 MB.'})
    return filename, content


def _private_response(data, *, status=200):
    response = Response(data, status=status)
    response['Cache-Control'] = 'private, no-store'
    return response


class _WorkbookUploadView(APIView):
    permission_classes = [IsAuthenticated, HasModuleAccess, CanUploadPortfolioWorkbook]
    module_required = 'executive_dashboard'
    # The shared route guard checks executive read; the dedicated permission
    # above separately enforces Project Control update for both upload actions.
    permission_action = 'read'
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ['post', 'options']


class PortfolioWorkbookPreviewView(_WorkbookUploadView):
    def post(self, request):
        filename, content = _upload(request)
        active_snapshot = PortfolioSource.objects.filter(key='poc').values_list('active_snapshot_id', flat=True).first()
        try:
            result = import_workbook(content, original_filename=filename, dry_run=True)
        except ValueError as exc:
            raise ValidationError({'file': str(exc)}) from exc
        except Exception as exc:
            logger.warning('Portfolio workbook preview could not parse input (%s)', type(exc).__name__)
            raise ValidationError({'file': 'The workbook could not be read. Save a valid .xlsx copy and try again.'}) from exc
        token = signing.dumps({
            'user_id': str(request.user.pk), 'sha256': result['sha256'], 'source_key': 'poc',
            'active_snapshot_id': active_snapshot, 'parser_version': PARSER_VERSION,
        }, salt=PREVIEW_SALT)
        return _private_response({**result, 'preview_token': token, 'expires_in_seconds': PREVIEW_MAX_AGE})


class PortfolioWorkbookImportView(_WorkbookUploadView):
    def post(self, request):
        filename, content = _upload(request)
        token = request.data.get('preview_token')
        if not isinstance(token, str) or not token or len(token) > 4000:
            raise ValidationError({'preview_token': 'Preview the workbook before importing it.'})
        try:
            preview = signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
        except signing.SignatureExpired:
            return _private_response({'detail': 'This preview expired. Preview the workbook again.', 'code': 'preview_expired'}, status=400)
        except signing.BadSignature:
            return _private_response({'detail': 'This preview is invalid. Preview the workbook again.', 'code': 'invalid_preview'}, status=400)
        if not isinstance(preview, dict) or preview.get('source_key') != 'poc' or preview.get('parser_version') != PARSER_VERSION or 'active_snapshot_id' not in preview:
            return _private_response({'detail': 'This preview is no longer valid. Preview the workbook again.', 'code': 'invalid_preview'}, status=400)
        if preview.get('user_id') != str(request.user.pk):
            return _private_response({'detail': 'Preview this workbook using your own account before importing.', 'code': 'invalid_preview_user'}, status=403)
        if preview.get('sha256') != hashlib.sha256(content).hexdigest():
            return _private_response({'detail': 'The workbook changed after preview. Preview the selected file again.', 'code': 'file_changed'}, status=400)
        try:
            with transaction.atomic():
                result = import_workbook(content, original_filename=filename,
                                         expected_active_snapshot=preview['active_snapshot_id'])
                from apps.rbac.utils import create_audit_log
                create_audit_log(
                    user=request.user, action='file_upload',
                    resource_type='PortfolioSnapshot', resource_repr=result['file_name'],
                    metadata={'snapshot_id': result['snapshot_id'], 'source_key': 'poc',
                              'sha256': result['sha256'], 'reporting_date': result['reporting_date'],
                              'row_count': result['row_count'], 'previous_snapshot_id': preview['active_snapshot_id'],
                              'request_path': request.path, 'action_label': 'Import portfolio workbook'},
                )
        except PortfolioSnapshotChanged as exc:
            return _private_response({'detail': str(exc), 'code': 'source_changed'}, status=409)
        except ValueError as exc:
            raise ValidationError({'file': str(exc)}) from exc
        except DatabaseError:
            logger.exception('Portfolio workbook publication failed')
            return _private_response({'detail': 'The workbook could not be saved. Try again shortly.'}, status=503)
        except Exception as exc:
            logger.warning('Portfolio workbook import could not complete (%s)', type(exc).__name__)
            return _private_response({'detail': 'The workbook could not be imported. Preview a valid .xlsx copy and try again.'}, status=400)
        return _private_response(result)
