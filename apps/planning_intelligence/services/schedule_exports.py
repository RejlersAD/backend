"""Version-native schedule exports for APIs, Excel, CSV, and Primavera P6."""
from __future__ import annotations

import csv
import io
import json
import re
from types import SimpleNamespace

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..governance_serializers import GovernanceItemSerializer, ScheduleReviewSerializer
from ..models import ActivityAssignment
from ..schedule_serializers import (
    ActivityAssignmentSerializer, ActivityRelationshipSerializer, ScheduleActivitySerializer,
    ScheduleBaselineSerializer, ScheduleResourceSerializer, ScheduleVersionSerializer,
    ScheduleWBSNodeSerializer, WorkCalendarSerializer,
)
from .export_utils import generation_to_xer_bytes
from .project_controls import build_control_dashboard
from .planning_registers import risk_snapshot
from .planning_boundaries import accepted_input_validation, freeze_schedule_inputs, is_document_driven_version, calculation_inputs_current
from .schedule_export_contract import (
    EXPORT_SCHEMA_VERSION, ScheduleExportError, adapter_capability, export_capabilities, validate_export_model,
)


EXPORT_CONTENT_TYPES = {
    'json': 'application/json', 'csv': 'text/csv; charset=utf-8',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xer': 'application/octet-stream',
    'mspdi': 'application/xml; charset=utf-8', 'mspdi_zip': 'application/zip',
}


def _safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _filename_part(value, fallback):
    """Return a readable, path-safe ASCII filename component."""
    cleaned = re.sub(r'[^A-Za-z0-9_-]+', '_', str(value or '')).strip('_-')
    return cleaned[:80] or fallback


def schedule_snapshot(version):
    schedule = version.schedule
    project = schedule.project
    if version.status == 'baselined':
        baseline = version.baselines.filter(is_deleted=False, approved_at__isnull=False).order_by('-approved_at').first()
        if baseline is None:
            raise ScheduleExportError('This version has no approved baseline snapshot.', code='baseline_snapshot_missing')
        frozen = _safe(baseline.snapshot)
        inputs = frozen.get('accepted_inputs') or {}
        frozen_calendars = inputs.get('calendars') or []
        project_identity = inputs.get('project_identity')
        schedule_identity = inputs.get('schedule_identity')
        limitations = [] if inputs else ['Legacy baseline has no frozen calendar/source manifest; unavailable values were not reconstructed.']
        if project_identity is None or schedule_identity is None:
            limitations.append('Project and schedule display names were not retained in this baseline; current names were not substituted.')
        # Never rebuild an approved baseline from today's editable calendar or
        # source files. Older snapshots declare any missing frozen information.
        return {
            'schema_version': EXPORT_SCHEMA_VERSION, 'exported_at': timezone.now().isoformat(),
            'export_state': 'approved_baseline', 'baseline_id': baseline.pk,
            'baseline_approved_at': baseline.approved_at.isoformat(),
            'project': project_identity or {'id': inputs.get('project_id', project.pk), 'name': None},
            'schedule': schedule_identity or {'id': baseline.schedule_id, 'code': None, 'name': None},
            'version': frozen.get('version') or {}, 'calendar': next(
                (row for row in frozen_calendars if row['id'] == inputs.get('default_calendar_id')), None),
            'calendars': frozen_calendars, 'wbs': frozen.get('wbs') or [],
            'activities': frozen.get('activities') or [], 'relationships': frozen.get('relationships') or [],
            'resources': inputs.get('resources') or [], 'assignments': inputs.get('assignments') or [],
            'baselines': [{'id': baseline.pk, 'name': baseline.name, 'snapshot': frozen}],
            'controls': None, 'governance': {'items': [], 'reviews': []},
            'risk_register': frozen.get('risk_register') or [],
            'traceability': inputs,
            'limitations': limitations,
        }
    controls = build_control_dashboard(version, schedule.data_date) if version.activities.filter(is_deleted=False).exists() else None
    return _safe({
        'schema_version': EXPORT_SCHEMA_VERSION, 'exported_at': timezone.now(),
        'export_state': 'structured_draft',
        'project': {'id': project.id, 'name': project.name, 'client': project.client, 'phase': project.phase},
        'schedule': {
            'id': schedule.id, 'name': schedule.name, 'code': schedule.code,
            'status': schedule.status, 'planned_start': schedule.planned_start, 'data_date': schedule.data_date,
        },
        'version': ScheduleVersionSerializer(version).data,
        'calendar': WorkCalendarSerializer(schedule.default_calendar).data if schedule.default_calendar else None,
        'calendars': WorkCalendarSerializer(project.work_calendars.filter(is_deleted=False), many=True).data,
        'wbs': ScheduleWBSNodeSerializer(version.wbs_nodes.filter(is_deleted=False), many=True).data,
        'activities': ScheduleActivitySerializer(version.activities.filter(is_deleted=False), many=True).data,
        'relationships': ActivityRelationshipSerializer(version.relationships.filter(is_deleted=False), many=True).data,
        'resources': ScheduleResourceSerializer(project.schedule_resources.filter(is_deleted=False), many=True).data,
        'assignments': ActivityAssignmentSerializer(ActivityAssignment.objects.filter(
            activity__version=version, activity__is_deleted=False, is_deleted=False,
        ), many=True).data,
        'baselines': ScheduleBaselineSerializer(schedule.baselines.filter(is_deleted=False), many=True).data,
        'controls': controls,
        'readiness': accepted_input_validation(version),
        'risk_register': risk_snapshot(version),
        'traceability': freeze_schedule_inputs(version),
        'governance': {
            'items': GovernanceItemSerializer(version.governance_items.filter(is_deleted=False), many=True).data,
            'reviews': ScheduleReviewSerializer(version.governance_reviews.filter(is_deleted=False), many=True).data,
        },
    })


def _activities_csv(snapshot):
    stream = io.StringIO(newline='')
    fields = [
        'external_id', 'name', 'activity_type', 'duration_days', 'discipline', 'responsible_role',
        'planned_start', 'planned_finish', 'total_float_days', 'free_float_days', 'is_critical',
        'constraint_type', 'constraint_date',
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in snapshot['activities']:
        writer.writerow({field: row.get(field, '') for field in fields})
    return stream.getvalue().encode('utf-8-sig')


def _excel(snapshot):
    workbook = Workbook()
    workbook.remove(workbook.active)

    def add_sheet(name, rows):
        sheet = workbook.create_sheet(name[:31])
        rows = list(rows or [])
        if not rows:
            sheet.append(['No data'])
            return
        headers = list(rows[0])
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='4F46E5')
        for row in rows:
            values = [
                json.dumps(row.get(header), ensure_ascii=False) if isinstance(row.get(header), (dict, list))
                else row.get(header) for header in headers
            ]
            sheet.append([value if not isinstance(value, str) or len(value) <= 32000
                          else 'See complete value in Traceability JSON chunks.' for value in values])
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Uploaded content is data, never an Excel formula.
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(50, max(12, max(len(str(cell.value or '')) for cell in column) + 2))

    add_sheet('Activities', snapshot['activities'])
    add_sheet('WBS', snapshot['wbs'])
    add_sheet('Relationships', snapshot['relationships'])
    add_sheet('Resources', snapshot['resources'])
    add_sheet('Assignments', snapshot['assignments'])
    add_sheet('Calendars', snapshot.get('calendars') or [])
    add_sheet('Export validation', snapshot.get('export_validation') or [])
    add_sheet('Adapter', [snapshot.get('adapter') or {}])
    add_sheet('Controls WBS', (snapshot.get('controls') or {}).get('wbs_breakdown', []))
    add_sheet('Governance', snapshot['governance']['items'])
    add_sheet('Risk register', snapshot.get('risk_register') or [])
    # Preserve long source passages and nested metadata without Excel's
    # 32,767-character cell limit silently truncating the evidence.
    complete = json.dumps(snapshot, ensure_ascii=False)
    add_sheet('Traceability', [{'chunk': index // 30000 + 1, 'json': complete[index:index + 30000]}
                               for index in range(0, len(complete), 30000)])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _xer(version, snapshot):
    _validate_legacy_xer(version, snapshot)
    parent_codes = {row['id']: row['code'] for row in snapshot['wbs']}
    updates = {row['id']: row for row in (snapshot.get('controls') or {}).get('activities', [])}
    activities = []
    for row in snapshot['activities']:
        progress = updates.get(row['id'], {})
        activities.append({
            'id': row['external_id'], 'name': row['name'],
            'wbs_code': parent_codes.get(row.get('wbs_node')),
            'original_duration_days': float(row['duration_days']),
            'start_date': row['planned_start'], 'finish_date': row['planned_finish'],
            'total_float_days': float(row['total_float_days'] or 0),
            'is_milestone': row['is_milestone'], 'predecessors': [],
            'physical_progress_pct': progress.get('physical_progress_pct', 0),
            'remaining_duration_days': progress.get('remaining_duration_days'),
            'actual_start': progress.get('actual_start'), 'actual_finish': progress.get('actual_finish'),
        })
    external_ids = {row['id']: row['external_id'] for row in snapshot['activities']}
    logic = [{
        'activity_id': external_ids.get(row['successor']),
        'predecessor_id': external_ids.get(row['predecessor']),
        'type': row['relationship_type'], 'lag_days': float(row['lag_days']),
    } for row in snapshot['relationships']]
    wbs = [{
        'code': row['code'], 'name': row['name'], 'level': row['level'],
        'parent_code': parent_codes.get(row.get('parent')),
    } for row in snapshot['wbs']]
    generation = SimpleNamespace(
        project=version.schedule.project, generated_by=version.created_by,
        wbs=wbs, activities=activities, logic_matrix=logic,
    )
    return generation_to_xer_bytes(generation)


def _validate_legacy_xer(version, snapshot):
    """Reject semantics the retained legacy writer would silently replace."""
    calendar = snapshot.get('calendar') or {}
    issues = []
    expected_hours = float((version.schedule.project.calendar_overrides or {}).get('hours_per_day') or 8)
    if (calendar.get('working_weekdays') != [0, 1, 2, 3, 4] or calendar.get('exceptions')
            or float(calendar.get('hours_per_day') or 0) != expected_hours):
        issues.append({'code': 'xer_calendar_unsupported', 'message': 'The legacy writer cannot preserve this calendar or its exceptions.'})
    for row in snapshot['activities']:
        if any(row.get(field) is None for field in ('duration_days', 'planned_start', 'planned_finish', 'total_float_days')):
            issues.append({'code': 'xer_missing_input', 'entity_id': row['external_id'], 'message': 'Native export requires explicit duration, dates and calculated float.'})
        if row.get('constraint_type') not in {None, 'none'}:
            issues.append({'code': 'xer_constraint_unsupported', 'entity_id': row['external_id'], 'message': 'The legacy writer does not preserve activity constraints.'})
        if row.get('calendar') and row.get('calendar') != calendar.get('id'):
            issues.append({'code': 'xer_mixed_calendar_unsupported', 'entity_id': row['external_id'], 'message': 'The legacy writer cannot preserve mixed calendars.'})
    if issues:
        raise ScheduleExportError('This XER adapter cannot preserve the schedule. Use JSON or Excel.',
                                  code='xer_unsupported_semantics', issues=issues)


def generate_schedule_export(version, export_format):
    export_format = adapter_capability(export_format).get('canonical_format', export_format)
    capability = adapter_capability(export_format)
    if capability['status'] == 'unavailable':
        raise ScheduleExportError(f'{capability["name"]} is not implemented or validated.', issues=capability['limitations'])
    if export_format == 'xer' and (is_document_driven_version(version) or version.status == 'baselined'):
        raise ScheduleExportError('A validated XER adapter is not available for evidence-driven plans or approved baselines. Export JSON or Excel to preserve evidence and scheduling inputs.',
                                  issues=capability['limitations'])
    if version.status == 'baselined' and not capability['baseline']:
        raise ScheduleExportError('This adapter cannot preserve an approved baseline. Choose JSON or Excel.')
    if export_format in {'mspdi', 'mspdi_zip'} and version.status != 'baselined' and not calculation_inputs_current(version):
        raise ScheduleExportError('The calculation no longer matches the current accepted inputs. Recalculate before XML export.',
                                  code='mspdi_calculation_stale')
    snapshot = schedule_snapshot(version)
    for activity in snapshot['activities']:
        metadata = activity.get('metadata') or {}
        if metadata.get('duration_pending') or metadata.get('duration_source') == 'missing_source':
            activity['duration_days'] = None
    readiness = snapshot.get('readiness') or {}
    if readiness.get('policy') == 'document_driven' and not readiness.get('ready_for_calculation'):
        for activity in snapshot['activities']:
            activity['total_float_days'] = activity['free_float_days'] = activity['is_critical'] = None
        snapshot['calculation_status'] = 'blocked'
    snapshot['adapter'] = capability
    snapshot['export_capabilities'] = export_capabilities()
    snapshot['export_validation'] = validate_export_model(snapshot)
    if snapshot['export_validation']:
        raise ScheduleExportError('Resolve invalid identifiers, dates or relationship references before exporting.',
                                  code='schedule_export_invalid', issues=snapshot['export_validation'])
    if export_format == 'json':
        content = json.dumps(snapshot, ensure_ascii=False, indent=2).encode('utf-8')
    elif export_format == 'csv':
        content = _activities_csv(snapshot)
    elif export_format == 'xlsx':
        content = _excel(snapshot)
    elif export_format in {'mspdi', 'mspdi_zip'}:
        from .ms_project_export import build_mspdi, mspdi_bundle
        content = mspdi_bundle(snapshot) if export_format == 'mspdi_zip' else build_mspdi(snapshot)[0]
    else:
        content = _xer(version, snapshot)
    project_name = _filename_part(snapshot['project'].get('name'), 'Project')
    schedule_code = _filename_part(snapshot['schedule'].get('code'), 'Schedule')
    export_label = 'Activities' if export_format == 'csv' else 'Schedule'
    extension = capability.get('extension', export_format)
    filename = f'{project_name}_{schedule_code}_v{version.version}_{export_label}.{extension}'
    return content, EXPORT_CONTENT_TYPES[export_format], filename
