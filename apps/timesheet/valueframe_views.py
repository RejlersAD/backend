"""ValueFrame reconciliation API (HR / payroll managers only).

    POST   valueframe/imports/                  upload a Wage Type Report (.xlsx)
    GET    valueframe/imports/                  list imports, newest period first
    GET    valueframe/imports/<id>/comparison/  booked vs present, per employee
    DELETE valueframe/imports/<id>/             remove an import (e.g. wrong file)
"""
from __future__ import annotations

import logging

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ValueFrameEntry, ValueFrameImport
from .valueframe import compare_import, parse_wage_type_report

logger = logging.getLogger(__name__)
FORBIDDEN = {'detail': 'HR or payroll manager access is required.'}


def _allowed(user):
    from .views import _can_manage_attendance
    return _can_manage_attendance(user)


def _import_payload(item):
    return {
        'id': item.id, 'period_start': item.period_start, 'period_end': item.period_end,
        'company': item.company, 'file_name': item.file_name, 'employee_count': item.employee_count,
        'warnings': item.warnings, 'uploaded_at': item.uploaded_at,
        'uploaded_by': (item.uploaded_by.get_full_name() or item.uploaded_by.email) if item.uploaded_by else '',
    }


def _audit(request, action, item, extra=None):
    try:
        from apps.rbac.utils import create_audit_log
        create_audit_log(user=request.user, action=action, resource_type='ValueFrameImport',
                         resource_repr=str(item), metadata={'valueframe_import_id': item.id, **(extra or {})},
                         ip_address=request.META.get('REMOTE_ADDR'), user_agent=request.META.get('HTTP_USER_AGENT', ''))
    except Exception:
        logger.exception('ValueFrame audit log failed')


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def valueframe_imports(request):
    if not _allowed(request.user):
        return Response(FORBIDDEN, status=status.HTTP_403_FORBIDDEN)
    if request.method == 'GET':
        return Response({'results': [_import_payload(i) for i in ValueFrameImport.objects.select_related('uploaded_by')[:60]]})

    upload = request.FILES.get('file')
    if not upload:
        return Response({'detail': 'Choose the ValueFrame Wage Type Report (.xlsx).'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        parsed = parse_wage_type_report(upload)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    replace = str(request.data.get('replace', '')).lower() in ('1', 'true', 'yes')
    same_period = ValueFrameImport.objects.filter(period_start=parsed['period_start'], period_end=parsed['period_end'])
    if same_period.exists() and not replace:
        existing = same_period.first()
        return Response({'detail': f'A ValueFrame report for {existing.period_start:%d %b %Y} – {existing.period_end:%d %b %Y} '
                                   'is already uploaded. Replace it?', 'conflict_import': _import_payload(existing)},
                        status=status.HTTP_409_CONFLICT)
    with transaction.atomic():
        if replace:
            same_period.delete()
        item = ValueFrameImport.objects.create(
            period_start=parsed['period_start'], period_end=parsed['period_end'], company=parsed['company'][:255],
            file_name=str(upload.name)[:255], file_sha256=parsed['sha256'], employee_count=len(parsed['rows']),
            warnings=parsed['warnings'][:50], uploaded_by=request.user,
        )
        ValueFrameEntry.objects.bulk_create([ValueFrameEntry(vf_import=item, **row) for row in parsed['rows']])
    _audit(request, 'create', item, {'employees': item.employee_count, 'replaced': replace})
    return Response({'import': _import_payload(item), **compare_import(item)}, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def valueframe_comparison(request, pk):
    if not _allowed(request.user):
        return Response(FORBIDDEN, status=status.HTTP_403_FORBIDDEN)
    item = ValueFrameImport.objects.select_related('uploaded_by').filter(pk=pk).first()
    if item is None:
        return Response({'detail': 'Import not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response({'import': _import_payload(item), **compare_import(item)})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def valueframe_import_detail(request, pk):
    if not _allowed(request.user):
        return Response(FORBIDDEN, status=status.HTTP_403_FORBIDDEN)
    item = ValueFrameImport.objects.filter(pk=pk).first()
    if item is None:
        return Response({'detail': 'Import not found.'}, status=status.HTTP_404_NOT_FOUND)
    _audit(request, 'delete', item)
    item.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
