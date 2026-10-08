"""Durable Equipment Register draft commands for Process Equipment List."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Exists, Max, OuterRef, Prefetch
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.project_organizer.views import _get_accessible_project

from .equipment_metadata import (
    build_equipment_metadata,
    equipment_master_view,
    normalise_equipment_metadata,
    synchronise_equipment_metadata,
)

from .models import (
    EquipmentItem,
    EquipmentItemChange,
    EquipmentRegister,
    EquipmentRevision,
)


ITEM_FIELDS = (
    'revision_label', 'tag', 'description', 'equipment_type',
    'design_flowrate', 'oper_pressure', 'oper_temperature',
    'design_pressure_min', 'design_pressure_max',
    'design_temp_min', 'design_temp_max', 'moc', 'insulation',
    'dimension_length', 'dimension_diameter', 'motor_rating',
    'pid_no', 'quality_required', 'phase', 'remarks',
    'source_locator', 'metadata', 'confidence', 'review_state',
)
TEXT_ITEM_FIELDS = tuple(
    field for field in ITEM_FIELDS
    if field not in {'source_locator', 'metadata', 'confidence', 'review_state'}
)
EXTRACTION_ALIASES = {
    'revision': 'revision_label',
    'type': 'equipment_type',
}
MAX_IMPORT_ROWS = 5000
MAX_SOURCE_FILES = 100
MAX_CHANGE_EVENTS = 1000


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


def _equipment_items_with_change_state():
    manual_changes = EquipmentItemChange.objects.filter(
        item_id=OuterRef('pk'), source=EquipmentItemChange.Source.MANUAL,
    )
    return EquipmentItem.objects.annotate(has_manual_changes=Exists(manual_changes))


def _registers_with_current_items(queryset=None):
    queryset = queryset if queryset is not None else EquipmentRegister.objects.all()
    return queryset.select_related('current_revision').prefetch_related(
        Prefetch('current_revision__items', queryset=_equipment_items_with_change_state()),
    )


def _serialize_item(item: EquipmentItem) -> dict:
    if getattr(item, 'has_manual_changes', False):
        item_status = 'Changed'
    elif item.review_state == EquipmentItem.ReviewState.REVIEWED:
        item_status = 'Reviewed'
    elif item.review_state == EquipmentItem.ReviewState.DISCREPANCY:
        item_status = 'Warnings'
    else:
        item_status = 'Unchanged'
    return {
        'id': str(item.id),
        'sl_no': item.sort_order,
        'revision': item.revision_label,
        'tag': item.tag,
        'description': item.description,
        'type': item.equipment_type,
        'equipment_type': item.equipment_type,
        'design_flowrate': item.design_flowrate,
        'oper_pressure': item.oper_pressure,
        'oper_temperature': item.oper_temperature,
        'design_pressure_min': item.design_pressure_min,
        'design_pressure_max': item.design_pressure_max,
        'design_temp_min': item.design_temp_min,
        'design_temp_max': item.design_temp_max,
        'moc': item.moc,
        'insulation': item.insulation,
        'dimension_length': item.dimension_length,
        'dimension_diameter': item.dimension_diameter,
        'motor_rating': item.motor_rating,
        'pid_no': item.pid_no,
        'quality_required': item.quality_required,
        'phase': item.phase,
        'remarks': item.remarks,
        'source_locator': item.source_locator or {},
        'metadata': item.metadata or {},
        'equipment_master': equipment_master_view(item.metadata),
        'confidence': str(item.confidence) if item.confidence is not None else None,
        'review_state': item.review_state,
        'status': item_status,
        'updated_at': item.updated_at.isoformat(),
    }


def _serialize_register(register: EquipmentRegister) -> dict:
    revision = register.current_revision
    items = list(revision.items.all()) if revision else []
    changed_items = sum(bool(getattr(item, 'has_manual_changes', False)) for item in items)
    return {
        'id': str(register.id),
        'project_id': str(register.project_id),
        'enterprise_project_id': register.enterprise_project_id,
        'register_number': register.register_number,
        'name': register.name,
        'discipline': register.discipline,
        'status': register.status,
        'is_active': register.is_active,
        'updated_at': register.updated_at.isoformat(),
        'revision': None if revision is None else {
            'id': str(revision.id),
            'number': revision.number,
            'status': revision.status,
            'is_immutable': revision.is_immutable,
            'version': revision.version,
            'source_upload_id': revision.source_upload_id,
            'source_files': revision.source_files or [],
            'extraction_run': revision.extraction_run or {},
            'created_at': revision.created_at.isoformat(),
            'updated_at': revision.updated_at.isoformat(),
            'summary': {
                'changed_items': changed_items,
                'reviewed_items': sum(item.review_state == EquipmentItem.ReviewState.REVIEWED for item in items),
                'discrepancies': sum(item.review_state == EquipmentItem.ReviewState.DISCREPANCY for item in items),
                'unreviewed_items': sum(item.review_state == EquipmentItem.ReviewState.UNREVIEWED for item in items),
            },
        },
        'items': [_serialize_item(item) for item in items],
    }


def _normalise_item(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f'Row {index + 1} must be an object.')
    values = {}
    for source_key, value in raw.items():
        key = EXTRACTION_ALIASES.get(source_key, source_key)
        if key not in ITEM_FIELDS:
            continue
        if key in TEXT_ITEM_FIELDS:
            text = '' if value is None else str(value).strip()
            max_length = EquipmentItem._meta.get_field(key).max_length
            if max_length and len(text) > max_length:
                raise ValueError(
                    f'Row {index + 1} {key} must be at most {max_length} characters.'
                )
            values[key] = text
        elif key == 'source_locator':
            values[key] = value if isinstance(value, dict) else {}
        elif key == 'metadata':
            values[key] = normalise_equipment_metadata(value)
        elif key == 'review_state':
            valid = {choice for choice, _ in EquipmentItem.ReviewState.choices}
            values[key] = value if value in valid else EquipmentItem.ReviewState.UNREVIEWED
        elif key == 'confidence':
            if value in ('', None):
                values[key] = None
            else:
                try:
                    confidence = Decimal(str(value))
                except (InvalidOperation, TypeError, ValueError) as exc:
                    raise ValueError(f'Row {index + 1} confidence must be numeric.') from exc
                if confidence < 0 or confidence > 100:
                    raise ValueError(f'Row {index + 1} confidence must be between 0 and 100.')
                values[key] = confidence
    tag = values.get('tag', '')
    if not tag:
        raise ValueError(f'Row {index + 1} requires an equipment tag.')
    if 'metadata' not in values:
        values['metadata'] = build_equipment_metadata({**raw, **values})
    return values


def _load_accessible_register(user, register_id, *, lock=False):
    queryset = EquipmentRegister.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related('project', 'current_revision')
    register = queryset.filter(pk=register_id).first()
    if register is None:
        return None, Response({'error': 'Equipment Register not found.'}, status=status.HTTP_404_NOT_FOUND)
    _, error = _get_accessible_project(user, register.project_id, lock=lock)
    if error:
        return None, error
    return register, None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_equipment_register(request):
    project_id = request.query_params.get('project_id')
    if not project_id:
        return Response({'error': 'project_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
    project, error = _get_accessible_project(request.user, project_id)
    if error:
        return error
    register = (
        _registers_with_current_items()
        .filter(project=project, is_active=True)
        .order_by('-updated_at')
        .first()
    )
    if register is None:
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(_serialize_register(register))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def import_equipment_extraction(request):
    project_id = request.data.get('project_id')
    source_upload_id = str(request.data.get('source_upload_id') or '').strip()
    raw_items = request.data.get('items')
    if not project_id:
        return Response({'error': 'project_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if not source_upload_id:
        return Response({'error': 'source_upload_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(raw_items, list) or not raw_items:
        return Response({'error': 'items must be a non-empty list.'}, status=status.HTTP_400_BAD_REQUEST)
    if len(raw_items) > MAX_IMPORT_ROWS:
        return Response({'error': f'At most {MAX_IMPORT_ROWS} items can be imported.'}, status=status.HTTP_400_BAD_REQUEST)
    drawing_ref = str(request.data.get('drawing_ref') or '').strip()
    if len(drawing_ref) > 300:
        return Response({'error': 'drawing_ref must be at most 300 characters.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        items = [
            _normalise_item(
                {**raw, 'pid_no': raw.get('pid_no') or raw.get('drawing_ref') or drawing_ref}
                if isinstance(raw, dict) else raw,
                index,
            )
            for index, raw in enumerate(raw_items)
        ]
    except ValueError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    tags = [item['tag'].casefold() for item in items]
    if len(tags) != len(set(tags)):
        return Response({'error': 'Equipment tags must be unique within a revision.'}, status=status.HTTP_400_BAD_REQUEST)
    source_files = request.data.get('source_files', [])
    if not isinstance(source_files, list) or len(source_files) > MAX_SOURCE_FILES:
        return Response(
            {'error': f'source_files must be a list of at most {MAX_SOURCE_FILES} filenames.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    source_files = [str(name).strip() for name in source_files]
    if any(not name or len(name) > 255 for name in source_files):
        return Response(
            {'error': 'Each source filename must contain 1 to 255 characters.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    with transaction.atomic():
        project, error = _get_accessible_project(request.user, project_id, lock=True)
        if error:
            return error
        register = (
            EquipmentRegister.objects.select_for_update()
            .filter(project=project, is_active=True)
            .order_by('-updated_at').first()
        )
        if register is None:
            suffix = (project.code or str(project.project_id)[:8]).strip().upper()[:61]
            register = EquipmentRegister.objects.create(
                project=project,
                enterprise_project_id=project.enterprise_project_id,
                register_number=f'EL-{suffix}',
                name='Equipment List',
                discipline=project.discipline or 'Process',
                created_by=request.user,
                updated_by=request.user,
            )
        existing = (
            register.revisions.prefetch_related('items')
            .filter(source_upload_id=source_upload_id).first()
        )
        if existing:
            if register.current_revision_id != existing.id:
                return Response({
                    'error': 'This extraction upload was already imported into an earlier revision.',
                    'code': 'source_upload_already_imported',
                }, status=status.HTTP_409_CONFLICT)
            register = _registers_with_current_items().get(pk=register.pk)
            return Response(_serialize_register(register))

        previous = register.current_revision
        if previous and previous.status == EquipmentRevision.Status.DRAFT:
            previous.status = EquipmentRevision.Status.SUPERSEDED
            previous.is_immutable = True
            previous.version += 1
            previous.save(update_fields=['status', 'is_immutable', 'version', 'updated_at'])
        next_number = (register.revisions.aggregate(value=Max('number'))['value'] or 0) + 1
        revision = EquipmentRevision.objects.create(
            register=register,
            number=next_number,
            source_upload_id=source_upload_id,
            source_files=source_files,
            extraction_run={
                'drawing_ref': drawing_ref,
                'mode': str(request.data.get('extraction_mode') or 'ai'),
            },
            created_by=request.user,
        )
        created_items = []
        changes = []
        for index, values in enumerate(items, start=1):
            item = EquipmentItem(revision=revision, sort_order=index, **values)
            created_items.append(item)
        EquipmentItem.objects.bulk_create(created_items, batch_size=500)
        saved_items = list(revision.items.all())
        for item in saved_items:
            changes.append(EquipmentItemChange(
                item=item, field='__row__', old_value=None,
                new_value={'tag': item.tag}, source=EquipmentItemChange.Source.AI,
                reason='Created from Equipment List extraction.', changed_by=request.user,
            ))
        EquipmentItemChange.objects.bulk_create(changes, batch_size=500)
        register.current_revision = revision
        register.status = EquipmentRegister.Status.DRAFT
        register.enterprise_project_id = project.enterprise_project_id
        register.updated_by = request.user
        register.save(update_fields=[
            'current_revision', 'status', 'enterprise_project', 'updated_by', 'updated_at',
        ])
        return Response(_serialize_register(register), status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_equipment_item(request, register_id, item_id):
    expected_version = request.data.get('expected_revision_version')
    if expected_version is None:
        return Response(
            {'error': 'expected_revision_version is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        return Response({'error': 'expected_revision_version must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
    patch = request.data.get('set')
    if not isinstance(patch, dict):
        return Response({'error': 'set must be an object.'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        register, error = _load_accessible_register(request.user, register_id, lock=True)
        if error:
            return error
        revision = EquipmentRevision.objects.select_for_update().filter(pk=register.current_revision_id).first()
        if revision is None:
            return Response({'error': 'The register has no current revision.'}, status=status.HTTP_409_CONFLICT)
        if revision.status != EquipmentRevision.Status.DRAFT or revision.is_immutable:
            return Response({'error': 'Only a draft revision can be edited.'}, status=status.HTTP_409_CONFLICT)
        if revision.version != expected_version:
            return Response({
                'error': 'This Equipment List changed after you opened it.',
                'code': 'stale_revision',
                'current_revision_version': revision.version,
            }, status=status.HTTP_409_CONFLICT)
        item = EquipmentItem.objects.select_for_update().filter(pk=item_id, revision=revision).first()
        if item is None:
            return Response({'error': 'Equipment item not found in the current revision.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            values = _normalise_item({**_serialize_item(item), **patch}, item.sort_order - 1)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        changed = []
        for field in ITEM_FIELDS:
            if field not in patch and field not in EXTRACTION_ALIASES.values():
                continue
            new_value = values.get(field, getattr(item, field))
            old_value = getattr(item, field)
            if old_value == new_value:
                continue
            setattr(item, field, new_value)
            changed.append((field, old_value, new_value))
        if not changed:
            register = _registers_with_current_items().get(pk=register.pk)
            return Response(_serialize_register(register))
        if any(field == 'tag' for field, _, _ in changed):
            if EquipmentItem.objects.filter(revision=revision, tag__iexact=item.tag).exclude(pk=item.pk).exists():
                return Response({'error': 'Equipment tag already exists in this revision.'}, status=status.HTTP_400_BAD_REQUEST)
        update_fields = [field for field, _, _ in changed]
        if 'metadata' not in patch and any(field in TEXT_ITEM_FIELDS for field, _, _ in changed):
            item.metadata = synchronise_equipment_metadata(_serialize_item(item), item.metadata)
            update_fields.append('metadata')
        item.save(update_fields=update_fields + ['updated_at'])
        EquipmentItemChange.objects.bulk_create([
            EquipmentItemChange(
                item=item, field=field,
                old_value=_json_value(old_value), new_value=_json_value(new_value),
                source=EquipmentItemChange.Source.MANUAL,
                reason=str(request.data.get('reason') or '').strip(),
                changed_by=request.user,
            )
            for field, old_value, new_value in changed
        ])
        revision.version += 1
        revision.save(update_fields=['version', 'updated_at'])
        register.updated_by = request.user
        register.save(update_fields=['updated_by', 'updated_at'])
        register.current_revision = revision
        register = _registers_with_current_items().get(pk=register.pk)
        return Response(_serialize_register(register))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def equipment_register_changes(request, register_id):
    """Return bounded, current-revision change evidence for workspace/history views."""
    register, error = _load_accessible_register(request.user, register_id)
    if error:
        return error
    revision = register.current_revision
    if revision is None:
        return Response({'revision_id': None, 'changes': [], 'truncated': False})

    changes = EquipmentItemChange.objects.filter(
        item__revision=revision,
    ).select_related('item', 'changed_by')
    item_id = request.query_params.get('item_id')
    if item_id:
        changes = changes.filter(item_id=item_id)
    total = changes.count()
    rows = changes.order_by('-created_at', '-id')[:MAX_CHANGE_EVENTS]
    return Response({
        'revision_id': str(revision.id),
        'revision_number': revision.number,
        'revision_version': revision.version,
        'total': total,
        'truncated': total > MAX_CHANGE_EVENTS,
        'changes': [{
            'id': str(change.id),
            'item_id': str(change.item_id),
            'tag': change.item.tag,
            'field': change.field,
            'old_value': change.old_value,
            'new_value': change.new_value,
            'source': change.source,
            'reason': change.reason,
            'changed_by': (
                change.changed_by.get_full_name()
                or change.changed_by.username
                or 'Unknown user'
            ),
            'changed_at': change.created_at.isoformat(),
        } for change in rows],
    })
