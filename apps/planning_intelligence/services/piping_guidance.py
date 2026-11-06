"""Explicit, revision-specific application of the user's piping planning guidance.

This is planner input, not extracted document evidence. Only an explicit apply
command enables it for a schedule version; approved history is never rewritten.
"""
from collections import defaultdict, deque
from decimal import Decimal
import re

from django.db import transaction
from django.utils import timezone

from ..access import can_write_project
from ..models import ActivityRelationship, ScheduleVersion
from .audit import record_event
from .cpm import WorkdayCalendar
from .schedule_approval import ScheduleApprovalError, current_schedule_version


VERSION = 'piping-department-guidance/1'
SOURCE = 'Planning department guidance and user-confirmed vendor sequence, revisions and working-day assumptions'
REVISIONS = {'IFR', 'IFA', 'IFC'}
EVENTS = {'vendor_offers', 'offer_receipt', 'po_placement', 'vendor_input', 'model_30',
          *{f'{kind}_{percent}' for kind in ('workshop', 'closeout') for percent in (30, 60, 90)}}
DURATIONS = {'vendor_offers': 15, 'offer_receipt': 5, 'po_placement': 10, 'vendor_input': 10,
             'closeout_30': 5, 'closeout_60': 10, 'closeout_90': 5}


def _rule(predecessor, successor, kind='FS', lag=0):
    return predecessor, successor, kind, lag


# Stage transitions inside a deliverable are retained separately. An explicit
# cross-revision dependency below is intentional and takes priority over the
# general equivalent-revision rule.
RULES = [
    _rule(('process_basis', 'IFR'), ('piping_basis', 'IFR')),
    _rule(('pids', 'IFR'), ('pms', 'IFR')),
    _rule(('piping_basis', 'IFR'), ('pms', 'IFR')),
    _rule(('pms', 'IFR'), ('valve_spec', 'IFR')),
    _rule(('valve_spec', 'IFR'), ('valve_datasheet', 'IFR')),
    _rule(('valve_datasheet', 'IFR'), ('valve_mr', 'IFR')),
    _rule(('valve_mr', 'IFA'), ('vendor_offers', 'EVENT')),
    _rule(('vendor_offers', 'EVENT'), ('offer_receipt', 'EVENT')),
    _rule(('offer_receipt', 'EVENT'), ('valve_tbe', 'IFC')),
    _rule(('valve_tbe', 'IFC'), ('po_placement', 'EVENT')),
    _rule(('po_placement', 'EVENT'), ('vendor_input', 'EVENT')),
    _rule(('pids', 'IFR'), ('plot_plan', 'IFR'), 'FF', 3),
    _rule(('piping_basis', 'IFR'), ('model_procedure', 'IFR'), 'FF', 4),
    _rule(('plot_plan', 'IFR'), ('model_procedure', 'IFR')),
    _rule(('model_procedure', 'IFR'), ('model_30', 'EVENT')),
    _rule(('pms', 'IFR'), ('model_30', 'EVENT')),
    _rule(('model_30', 'EVENT'), ('workshop_30', 'EVENT')),
    _rule(('workshop_30', 'EVENT'), ('report_30', 'IFR')),
    _rule(('report_30', 'IFR'), ('closeout_30', 'EVENT')),
    _rule(('closeout_30', 'EVENT'), ('closeout_report_30', 'IFR')),
    _rule(('closeout_report_30', 'IFA'), ('workshop_60', 'EVENT')),
    _rule(('vendor_input', 'EVENT'), ('workshop_60', 'EVENT')),
    _rule(('workshop_60', 'EVENT'), ('report_60', 'IFR')),
    _rule(('report_60', 'IFR'), ('closeout_60', 'EVENT')),
    _rule(('closeout_60', 'EVENT'), ('isometrics', 'IFI')),
    _rule(('closeout_60', 'EVENT'), ('closeout_report_60', 'IFR')),
    _rule(('isometrics', 'IFI'), ('piping_mto', 'IFI')),
    _rule(('isometrics', 'IFI'), ('stress_report', 'IFR')),
    _rule(('closeout_report_60', 'IFA'), ('workshop_90', 'EVENT')),
    _rule(('workshop_90', 'EVENT'), ('report_90', 'IFR')),
    _rule(('report_90', 'IFR'), ('closeout_90', 'EVENT')),
    _rule(('closeout_90', 'EVENT'), ('isometrics', 'IFF')),
    _rule(('closeout_90', 'EVENT'), ('piping_ga', 'IFR')),
    _rule(('closeout_90', 'EVENT'), ('closeout_report_90', 'IFR')),
    _rule(('closeout_report_90', 'IFA'), ('piping_ga', 'IFA')),
    _rule(('closeout_report_90', 'IFA'), ('piping_mto', 'IFF')),
]


def _normal(value):
    value = str(value or '').lower().replace('&', ' and ')
    for old, new in (('recepit', 'receipt'), ('proceedure', 'procedure'), ('activites', 'activities'),
                     ('modeling', 'modelling')):
        value = value.replace(old, new)
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


ALIASES = {
    'process_basis': ['Process Design Basis'], 'piping_basis': ['Piping Design Basis'],
    'pids': ['P&IDs', 'P&ID', 'Piping and Instrumentation Diagrams', 'Piping and Instrumentation Diagram'],
    'pms': ['Piping Material Specification', 'Piping Material Specification PMS', 'Piping Material Specifications', 'PMS'],
    'valve_spec': ['Specification for Valves', 'Specifications for Valves', 'Valve Specification', 'Valve Specifications'],
    'valve_datasheet': ['Datasheet for Valves', 'Datasheets for Valves', 'Data Sheets for Valves', 'Valve Datasheet', 'Valve Datasheets'],
    'valve_mr': ['MR for Valves', 'Material Requisition for Valves', 'Material Requisition MR for Valves', 'Valve Material Requisition'],
    'vendor_offers': ['Valve Vendor Offers', 'Valve Vendor Offers Milestone'],
    'offer_receipt': ['Receipt of Valve Vendor Offer', 'Receipt of Valve Vendor Offer Milestone', 'Receipt of Valve Vendor Offers', 'Receipt of Vendor Offer'],
    'valve_tbe': ['TBE for Valves', 'Technical Bid Evaluation for Valves', 'Technical Bid Evaluation TBE for Valves', 'Valve TBE'],
    'po_placement': ['PO Placement of Valve Vendor', 'PO Placement of Valve Vendor Milestone', 'Valve PO Placement'],
    'vendor_input': ['Valve Vendor Input', 'Valve Vendor Input Milestone'],
    'plot_plan': ['Plot Plan'],
    'model_procedure': ['30% 3D Model Review Procedure', '30% Model Review Procedure', '3D Model Review Procedure'],
    'model_30': ['30% Modelling Activities Milestone', '30% Modelling Activities Completion', '30% Modelling Activities'],
    'isometrics': ['Isometrics', 'Isometric', 'Piping Isometrics', 'Isometric Drawings', 'Piping Isometric Drawings'],
    'piping_mto': ['Piping MTO', 'Piping Material Take Off'],
    'stress_report': ['Stress Report', 'Piping Stress Report', 'Piping Stress Analysis Report'],
    'piping_ga': ['Piping General Arrangement Drawings', 'Piping GA Drawings'],
}
for _percent in (30, 60, 90):
    ALIASES[f'workshop_{_percent}'] = [f'{_percent}% Model Review Workshop']
    ALIASES[f'report_{_percent}'] = [f'{_percent}% Model Review Report']
    ALIASES[f'closeout_{_percent}'] = [f'{_percent}% Closeout Modelling Activities {suffix}'
        for suffix in ('Completion', 'Completion Milestone')]
    ALIASES[f'closeout_report_{_percent}'] = [f'{_percent}% Model Review Closeout Report', f'{_percent}% Model Review Closeout']
FAMILIES = {_normal(alias): family for family, aliases in ALIASES.items() for alias in aliases}


def _gate_label(gate):
    family, stage = gate
    return ALIASES[family][0] + (f' ({stage})' if stage != 'EVENT' else '')


def activity_identity(activity):
    metadata = activity.metadata or {}
    source = metadata.get('source_deliverable')
    title = metadata.get('deliverable') or (source.get('title') if isinstance(source, dict) else None) or activity.name
    # Only explicit revisions are recognized. FINAL_ISSUE is never guessed to
    # mean IFC, IFI or IFF.
    explicit = re.search(r'\b(IFR|IFA|IFC|IFI|IFF)\b\s*\)?\s*$', activity.name.upper())
    stage = str(metadata.get('workflow_stage_code') or '').upper()
    label = str(metadata.get('workflow_stage_name') or '').upper()
    if stage not in {'IFR', 'IFA', 'IFC', 'IFI', 'IFF'}:
        stage = label if label in {'IFR', 'IFA', 'IFC', 'IFI', 'IFF'} else explicit[1] if explicit else stage
    clean = re.sub(r'\s*(?:[-–]\s*)?\(?(?:IFR|IFA|IFC|IFI|IFF|COMPANY REVIEW|COMPANY APPROVAL|FINAL ISSUE)\)?\s*$', '', str(title), flags=re.I)
    family = FAMILIES.get(_normal(clean))
    return family, 'EVENT' if family in EVENTS else stage


def enabled(version):
    return ((version.evidence_input_snapshot or {}).get('planner_adjustments') or {}).get('piping_guidance')


def _issue(code, message, **details):
    return {'code': code, 'severity': 'critical', 'message': message, **details}


def _context(version):
    from .planning_package_boundary import package_context
    proposal = package_context(version)
    schedule = version.schedule
    return (proposal['calendar'], proposal['start'], proposal['finish']) if proposal else (
        schedule.default_calendar, schedule.planned_start, schedule.project.planned_end_date)


def _deadline(version, activities):
    stored, start, finish = _context(version)
    if not stored or not stored.working_weekdays or stored.hours_per_day <= 0 or not start or not finish or finish < start:
        return {}, [_issue('piping_calendar_required', 'Save Project Start, Contractual Project Finish and a valid active project calendar.')]
    calendar = WorkdayCalendar(stored, start)
    total = calendar.index_of(calendar.on_or_before(finish)) + 1
    allowed = total * 9 // 10
    if allowed < 1:
        return {}, [_issue('piping_window_too_short', 'The 90% project window contains no complete working day.')]
    piping = [row for row in activities if _normal(row.discipline) in {'piping', 'piping engineering', 'piping discipline'}]
    if not piping:
        return {}, [_issue('piping_scope_missing', 'No Piping discipline activities are present in this schedule.')]
    latest = max((row.planned_finish for row in piping if row.planned_finish), default=None)
    return {'project_start': start.isoformat(), 'contractual_finish': finish.isoformat(),
            'project_working_days': total, 'piping_working_days': allowed,
            'contingency_working_days': total - allowed, 'finish_by': calendar.date_at(allowed - 1).isoformat(),
            'latest_piping_finish': latest.isoformat() if latest else None,
            'piping_activity_count': len(piping), 'calendar_id': stored.pk}, []


def preview_piping_guidance(version, *, actor=None):
    """Pure read preview with exact matches, removals, missing stages and cycles."""
    from .operational_jobs import schedule_state_fingerprint, canonical_fingerprint
    activities = list(version.activities.filter(is_deleted=False).order_by('pk'))
    by_id = {row.pk: row for row in activities}
    identities = {row.pk: activity_identity(row) for row in activities}
    gates = defaultdict(list)
    for pk, identity in identities.items():
        if identity[0]:
            gates[identity].append(pk)
    issues = [_issue('piping_gate_ambiguous', f'More than one activity matches {_gate_label((family, stage))}. Resolve the duplicate before applying.', activity_ids=ids)
              for (family, stage), ids in gates.items() if len(ids) > 1]
    for row in activities:
        code = str((row.metadata or {}).get('workflow_stage_code') or '').upper()
        label = re.search(r'\b(IFR|IFA|IFC|IFI|IFF)\b\s*\)?\s*$', row.name.upper())
        if identities[row.pk][0] and code in {'IFR', 'IFA', 'IFC', 'IFI', 'IFF'} and label and label[1] != code:
            issues.append(_issue('piping_stage_conflict', f'{row.external_id} has conflicting revision metadata and activity name. Confirm the exact stage before applying.', activity_ids=[row.pk]))
    present = {family for family, _stage in gates}
    pairs = {(pred[0], succ[0]) for pred, succ, _kind, _lag in RULES}
    pairs.update({('valve_mr', 'offer_receipt'), ('vendor_offers', 'valve_tbe')})
    desired, missing_scope = {}, set()
    for pred, succ, kind, lag in RULES:
        if pred[0] not in present or succ[0] not in present:
            missing_scope.update(family for family in (pred[0], succ[0]) if family not in present)
            if pred[0] in present or succ[0] in present:
                issues.append(_issue('piping_endpoint_missing', f'Required dependency endpoint absent: {_gate_label(pred)} -> {_gate_label(succ)}. Confirm the scope with the schedule owner; no activity is invented.'))
            continue
        revisions = [(pred, succ, kind, lag)]
        if pred[1] == succ[1] and pred[1] in REVISIONS:
            revisions += [((pred[0], stage), (succ[0], stage), 'FS', 0) for stage in sorted(REVISIONS - {pred[1]})
                          if (pred[0], stage) in gates and (succ[0], stage) in gates]
        for left, right, relation, offset in revisions:
            if left not in gates or right not in gates:
                issues.append(_issue('piping_stage_missing', f'Required stage missing: {_gate_label(left)} -> {_gate_label(right)}. Add or identify the exact stage; final issue is not substituted.'))
            elif len(gates[left]) == len(gates[right]) == 1:
                desired[(gates[left][0], gates[right][0], relation)] = offset
    stored_links = list(version.relationships.filter(is_deleted=False).order_by('pk'))
    existing = {(link.predecessor_id, link.successor_id, link.relationship_type): float(link.lag_days)
                for link in stored_links}
    removals = {key: lag for key, lag in existing.items()
                if (identities.get(key[0], (None,))[0], identities.get(key[1], (None,))[0]) in pairs
                and (key not in desired or desired[key] != lag)}
    # Disclosed generic category/wave gates must not continue to make an IFR
    # successor wait for a whole deliverable. Explicit unrelated manual/source
    # links remain and are checked for cycles rather than silently discarded.
    governed_successors = {key[1] for key in desired}
    for link in stored_links:
        key = (link.predecessor_id, link.successor_id, link.relationship_type)
        if (link.successor_id in governed_successors and key not in desired
                and identities.get(link.predecessor_id, (None,))[0] != identities.get(link.successor_id, (None,))[0]
                and (link.metadata or {}).get('source') in {'planning_sequence_proposal', 'discipline_gate', 'discipline_delivery_wave', 'model_review_offset'}):
            removals[key] = float(link.lag_days)
    additions = {key: lag for key, lag in desired.items() if existing.get(key) != lag}
    final = {key: lag for key, lag in existing.items() if key not in removals}
    final.update(desired)
    outgoing, indegree = defaultdict(list), dict.fromkeys(by_id, 0)
    for left, right, _kind in final:
        if left not in by_id or right not in by_id:
            issues.append(_issue('piping_link_outside_version', 'A relationship points outside this schedule version.'))
            continue
        outgoing[left].append(right)
        indegree[right] += 1
    queue, visited = deque(pk for pk, count in indegree.items() if not count), 0
    while queue:
        pk = queue.popleft()
        visited += 1
        for successor in outgoing[pk]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if visited != len(by_id):
        issues.append(_issue('piping_guidance_cycle', 'The proposed guidance conflicts with retained links and creates a cycle. Review those links first.'))
    durations = [{'activity_id': row.pk, 'name': row.name, 'previous_duration_days': float(row.duration_days),
                  'duration_days': DURATIONS[identities[row.pk][0]], 'previous_activity_type': row.activity_type}
                 for row in activities if identities[row.pk][0] in DURATIONS
                 and (row.duration_days != DURATIONS[identities[row.pk][0]] or row.is_milestone or row.activity_type != 'task')]
    if not desired:
        issues.append(_issue('piping_rules_not_matched', 'No complete piping dependency rule matches this schedule. Identify the actual deliverables and stages first.'))
    deadline, calendar_issues = _deadline(version, activities)
    issues.extend(calendar_issues)
    if deadline and any(row.calendar_id and row.calendar_id != deadline['calendar_id'] for row in activities):
        issues.append(_issue('piping_mixed_calendars', 'Align activity calendars with the active project calendar before applying working-day guidance.'))
    def rows(values):
        return [{'predecessor_id': key[0], 'successor_id': key[1], 'type': key[2], 'lag_days': lag,
                 'predecessor': by_id[key[0]].name, 'successor': by_id[key[1]].name}
                for key, lag in values.items() if key[0] in by_id and key[1] in by_id]
    plan = {'rule_version': VERSION, 'source': SOURCE, 'version_id': version.pk,
            'expected_updated_at': version.updated_at.isoformat(), 'state_fingerprint': schedule_state_fingerprint(version),
            'scope_fingerprint': canonical_fingerprint([{'id': row.pk, 'name': row.name, 'discipline': row.discipline,
                'activity_type': row.activity_type, 'calendar_id': row.calendar_id} for row in activities]),
            'add_links': rows(additions), 'remove_links': rows(removals), 'duration_changes': durations,
            'matched_rule_count': len(desired), 'missing_scope': [ALIASES[family][0] for family in sorted(missing_scope)], 'issues': issues,
            'deadline': deadline, 'enabled': bool(enabled(version)),
            'can_apply': version.status in {'draft', 'calculated'} and current_schedule_version(version) and not issues
                         and (actor is None or can_write_project(actor, version.schedule.project))}
    plan['preview_token'] = canonical_fingerprint(plan)
    return plan


def _write_plan(version, plan):
    now = timezone.now()
    for row in plan['remove_links']:
        version.relationships.filter(predecessor_id=row['predecessor_id'], successor_id=row['successor_id'],
            relationship_type=row['type'], is_deleted=False).update(is_deleted=True, deleted_at=now, updated_at=now)
    for row in plan['add_links']:
        previous = ActivityRelationship.objects.filter(version=version, predecessor_id=row['predecessor_id'],
            successor_id=row['successor_id'], relationship_type=row['type']).first()
        metadata = dict(previous.metadata or {}) if previous else {}
        if 'pre_guidance_metadata' not in metadata:
            metadata = {**metadata, 'pre_guidance_metadata': dict(metadata)}
        metadata.update(source='planning_department_guidance', rule_version=VERSION, lag_unit='working_days')
        ActivityRelationship.objects.update_or_create(version=version, predecessor_id=row['predecessor_id'],
            successor_id=row['successor_id'], relationship_type=row['type'],
            defaults={'lag_days': row['lag_days'], 'is_deleted': False, 'deleted_at': None, 'metadata': metadata})
    for change in plan['duration_changes']:
        activity = version.activities.get(pk=change['activity_id'])
        metadata = {**(activity.metadata or {}), 'piping_guidance_duration': VERSION,
                    'pre_guidance_duration': (activity.metadata or {}).get('pre_guidance_duration', change['previous_duration_days']),
                    'pre_guidance_activity_type': (activity.metadata or {}).get('pre_guidance_activity_type', change['previous_activity_type']),
                    'pre_guidance_duration_source': (activity.metadata or {}).get('pre_guidance_duration_source', (activity.metadata or {}).get('duration_source')),
                    'duration_source': 'planning_department_guidance', 'duration_unit': 'working_days'}
        # is_milestone is derived from activity_type by the existing model.
        activity.activity_type, activity.duration_days, activity.metadata = 'task', Decimal(change['duration_days']), metadata
        activity.save()


@transaction.atomic
def apply_piping_guidance(version, actor, *, preview_token):
    version = ScheduleVersion.objects.select_for_update().get(pk=version.pk, is_deleted=False)
    if not can_write_project(actor, version.schedule.project):
        raise ScheduleApprovalError('You cannot modify this schedule.', code='piping_guidance_forbidden', status_code=403)
    plan = preview_piping_guidance(version)
    if not plan['can_apply']:
        raise ScheduleApprovalError('Open a current editable revision and resolve the piping guidance findings.', code='piping_guidance_blocked', issues=plan['issues'])
    if not preview_token or preview_token != plan['preview_token']:
        raise ScheduleApprovalError('The schedule or preview changed. Review the piping guidance again.', code='version_conflict')
    if (enabled(version) and enabled(version).get('version') == VERSION
            and not plan['add_links'] and not plan['remove_links'] and not plan['duration_changes']):
        return {'version_id': version.pk, 'rule_version': VERSION, 'changed': False,
                'message': 'The current piping guidance is already applied.'}
    _write_plan(version, plan)
    snapshot = dict(version.evidence_input_snapshot or {})
    adjustments = dict(snapshot.get('planner_adjustments') or {})
    adjustments['piping_guidance'] = {'version': VERSION, 'source': SOURCE, 'applied_by_id': actor.pk,
                                    'applied_at': timezone.now().isoformat()}
    snapshot['planner_adjustments'] = adjustments
    version.evidence_input_snapshot = snapshot
    version.status, version.calculated_at, version.calculated_finish = 'draft', None, None
    version.save(update_fields=['evidence_input_snapshot', 'status', 'calculated_at', 'calculated_finish', 'updated_at'])
    version.assurance_reviews.filter(status__in=['draft', 'ready', 'approved']).update(status='superseded')
    record_event(project=version.schedule.project, actor=actor, action='schedule.piping_guidance_applied', entity=version,
                 before={'removed_links': plan['remove_links'], 'durations': plan['duration_changes']},
                 after={'rule_version': VERSION, 'added_links': plan['add_links'], 'deadline': plan['deadline'], 'missing_scope': plan['missing_scope']})
    return {'version_id': version.pk, 'rule_version': VERSION, 'matched_rule_count': plan['matched_rule_count'],
            'missing_scope': plan['missing_scope'], 'message': 'Piping guidance applied. Recalculate CPM and review schedule assurance.'}


def guidance_findings(version):
    if not enabled(version):
        return []
    plan = preview_piping_guidance(version)
    findings = list(plan['issues'])
    if plan['add_links'] or plan['remove_links'] or plan['duration_changes']:
        findings.append(_issue('piping_guidance_changed', 'The piping links, equivalent revisions or protected working-day durations differ from the applied guidance. Review and reapply the guidance.'))
    deadline = plan['deadline']
    if deadline.get('latest_piping_finish') and deadline['latest_piping_finish'] > deadline['finish_by']:
        findings.append(_issue('piping_ninety_percent_overrun', f"Piping forecasts {deadline['latest_piping_finish']} beyond the 90% working-day deadline {deadline['finish_by']}. Review the driving work; contractual finish and fixed durations remain unchanged.", **deadline))
    if plan['missing_scope']:
        findings.append({'code': 'piping_guidance_scope_gap', 'severity': 'warning',
                         'message': 'Guidance endpoints absent from the current scope: ' + ', '.join(plan['missing_scope'])})
    if enabled(version).get('version') != VERSION:
        findings.append(_issue('piping_guidance_version_changed', 'Review and apply the current piping guidance version.'))
    return findings


def reapply_after_reset(version):
    if enabled(version):
        plan = preview_piping_guidance(version)
        if plan['issues']:
            raise ScheduleApprovalError('The reset network cannot satisfy the applied piping guidance.', code='piping_guidance_reset_conflict', issues=plan['issues'])
        _write_plan(version, plan)
