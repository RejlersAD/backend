"""Evidence-bound delay sensitivity and internal review, never contract entitlement."""
from copy import deepcopy
from datetime import date, timedelta
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..access import can_final_approve_defaults
from ..models import (DelayAnalysisCase, DelayAnalysisRun, DelayEvent, EvidenceDocumentVersion,
    EvidenceNode, GovernanceItem, OperationalControlReport, PlanningAuditEvent, PlanningProject,
    PlanningRiskRecord, ScheduleBaseline)
from .audit import record_event
from .operational_controls import _baseline, _required, _write
from .operational_jobs import canonical_fingerprint
from .schedule_approval import ScheduleApprovalError


def _safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, allow_nan=False))


def _conflict(message, code='delay_case_conflict'):
    raise ScheduleApprovalError(message, code=code)


def _ids(baseline):
    return {row['id'] for row in baseline.snapshot.get('activities', [])}


def _schedule_snapshot(baseline):
    """Only schedule geometry. Never export rates, budgets or cost manifests."""
    original = baseline.snapshot
    activity_fields = ('id', 'external_id', 'name', 'activity_type', 'duration_days', 'calendar', 'calendar_id',
        'planned_start', 'planned_finish', 'constraint_type', 'constraint_date', 'wbs_node', 'is_critical', 'total_float_days')
    relationship_fields = ('id', 'predecessor', 'successor', 'predecessor_id', 'successor_id', 'relationship_type', 'lag_days')
    inputs = original.get('accepted_inputs') or {}
    return deepcopy({'activities': [{key: row[key] for key in activity_fields if key in row}
                                    for row in original.get('activities', [])],
        'relationships': [{key: row[key] for key in relationship_fields if key in row}
                          for row in original.get('relationships', [])],
        'accepted_inputs': {key: inputs.get(key) for key in ('project_start', 'project_finish', 'default_calendar_id', 'calendars')}})


def _report(project, baseline, key):
    report = get_object_or_404(OperationalControlReport, pk=key, project=project, baseline=baseline,
                             status='published', published_by__isnull=False, published_at__isnull=False)
    if report.publication.get('baseline_fingerprint') != canonical_fingerprint(baseline.snapshot):
        _conflict('The published report does not match this immutable baseline. Review its source integrity.', 'delay_reference_integrity')
    return report


def _reference(case):
    report = _report(case.project, case.baseline, case.reference_report_id)
    fields = ('activity_id', 'actual_start', 'actual_finish', 'physical_progress_pct', 'installed_quantity',
              'remaining_duration_days', 'evidence', 'notes')
    return {'report_id': report.pk, 'report_revision': report.revision, 'baseline_id': case.baseline_id,
        'baseline': _schedule_snapshot(case.baseline),
        'observations': [{key: row[key] for key in fields if key in row} for row in report.observations],
        'data_date': report.period_snapshot['data_date'],
        'forecast': deepcopy((report.publication.get('preview') or {}).get('forecast')),
        'rule_version': report.publication.get('rule_version'),
        'publication_fingerprint': report.publication.get('source_fingerprint'),
        'later_correction_ids': list(OperationalControlReport.objects.filter(project=case.project,
            baseline_id=case.baseline_id, reporting_period_id=report.reporting_period_id, status='published',
            created_at__gt=report.created_at).order_by('created_at', 'pk').values_list('pk', flat=True))}


def _document_reference(document):
    return {'filename': document.filename, 'file_sha256': document.file_sha256,
        'text_sha256': document.text_sha256, 'recorded_integrity_status': document.integrity_status,
        'storage_verification': 'not_rechecked'}


def _evidence(project, rows):
    result = []
    for row in rows:
        entry = {'reference': row['reference'], 'kind': 'planner_reference'}
        if row.get('document_version_id'):
            document = get_object_or_404(EvidenceDocumentVersion, pk=row['document_version_id'], graph__project=project)
            entry.update(document_version_id=str(document.pk), kind='document_reference',
                         document=_document_reference(document))
        if row.get('fact_id'):
            fact = get_object_or_404(EvidenceNode, pk=row['fact_id'], graph__project=project)
            if row.get('document_version_id') and str(fact.document_version_id) != str(row['document_version_id']):
                raise ValidationError('The cited fact must belong to the selected document version.')
            entry.update(fact_id=str(fact.pk), fact={'entity_id': fact.entity_id, 'property': fact.property,
                'kind': fact.kind, 'value': deepcopy(fact.value), 'unit': fact.unit,
                'provenance_type': fact.provenance_type, 'document_version_id': str(fact.document_version_id) if fact.document_version_id else None,
                'status': fact.status, 'current': fact.current, 'sources': deepcopy(fact.sources),
                'validation': deepcopy(fact.validation)}, kind='fact_reference')
            if fact.document_version_id and 'document' not in entry:
                document = get_object_or_404(EvidenceDocumentVersion, pk=fact.document_version_id, graph__project=project)
                entry.update(document_version_id=str(document.pk), document=_document_reference(document))
        result.append(entry)
    return result


def _evidence_issues(source):
    referenced = [row for event in source.get('events', []) for row in event.get('evidence', []) if row.get('document')]
    if not referenced:
        return []
    return [{'code': 'document_bytes_not_rechecked', 'severity': 'warning',
        'message': 'Document references retain the recorded version and hashes. Original file availability and bytes have not been rechecked in this case; review the cited evidence before deciding.'}]


def _event_snapshot(item, *, validate=True):
    row = {key: getattr(item, key) for key in ('id', 'revision', 'baseline_id', 'title', 'description',
            'start_date', 'end_date', 'status', 'activity_ids', 'governance_item_id', 'risk_id')}
    row.update(created_by_id=item.created_by_id, updated_by_id=item.updated_by_id, updated_at=item.updated_at,
               evidence=_evidence(item.project, item.evidence) if validate else deepcopy(item.evidence))
    if item.governance_item_id:
        source = item.governance_item
        if validate and (source.is_deleted or source.version_id != item.baseline.source_version_id
                or source.activity_id and source.activity_id not in item.activity_ids):
            _conflict('The linked governance item is no longer in this baseline scope.', 'delay_event_source_changed')
        row['governance_source'] = {key: getattr(source, key) for key in
            ('id', 'version_id', 'activity_id', 'item_type', 'title', 'description', 'status', 'resolution', 'updated_at')}
    if item.risk_id:
        source = item.risk
        if validate and source.version_id != item.baseline.source_version_id:
            _conflict('The linked risk is no longer in this baseline scope.', 'delay_event_source_changed')
        row['risk_source'] = {key: getattr(source, key) for key in
            ('id', 'version_id', 'source_key', 'revision', 'title', 'description', 'status', 'response', 'resolution', 'provenance')}
    return _safe(row)


def _events(case):
    rows = {row.pk: row for row in DelayEvent.objects.filter(project=case.project, baseline=case.baseline,
        pk__in=case.event_ids).select_related('project', 'baseline', 'governance_item', 'risk')}
    if len(rows) != len(case.event_ids) or not rows:
        raise ValidationError('Select distinct recorded events from this baseline.')
    return [_event_snapshot(rows[key]) for key in case.event_ids]


def _source(case):
    from .delay_analysis import DELAY_RULE_VERSION
    data = {'reference': _reference(case), 'events': _events(case), 'changes': case.changes,
            'scenarios': case.scenarios, 'recommendation': case.recommendation, 'rule_version': DELAY_RULE_VERSION}
    return _safe(data), canonical_fingerprint(data)


def _lock_sources(case):
    """Hold cited mutable rows stable through a calculation or review commit."""
    events = list(DelayEvent.objects.select_for_update().filter(
        project=case.project, baseline=case.baseline, pk__in=case.event_ids).order_by('pk'))
    references = [reference for event in events for reference in event.evidence]
    targets = (
        (GovernanceItem, [event.governance_item_id for event in events if event.governance_item_id]),
        (PlanningRiskRecord, [event.risk_id for event in events if event.risk_id]),
        (EvidenceDocumentVersion, [row['document_version_id'] for row in references if row.get('document_version_id')]),
        (EvidenceNode, [row['fact_id'] for row in references if row.get('fact_id')]),
    )
    for model, identifiers in targets:
        list(model.objects.select_for_update().filter(pk__in=identifiers).order_by('pk').values_list('pk', flat=True))


def _recommendation(case, result):
    request = case.recommendation or {}
    selected = next((row for row in result.get('scenarios', []) if row['id'] == request.get('selected_scenario_id')), None)
    selected = selected or result.get('impact') or {}
    days = request.get('requested_extension_calendar_days')
    contractual = (result.get('reference') or {}).get('contractual_finish')
    proposed = None
    if days is not None and contractual:
        try:
            proposed = (date.fromisoformat(contractual) + timedelta(days=days)).isoformat()
        except (ValueError, OverflowError):
            pass
    net = selected.get('net_finish_shift_calendar_days')
    warnings = []
    if days is not None and net is not None and days > max(0, net):
        warnings.append({'code': 'request_exceeds_modelled_increment',
            'message': 'The requested extension exceeds this scenario’s modelled incremental delay. Its separate contractual basis requires review.'})
    if days is not None and net is None:
        warnings.append({'code': 'request_has_no_calculated_support',
            'message': 'This model cannot establish the incremental timing effect; the requested days remain an unsupported proposal.'})
    return {'requested_extension_calendar_days': days, 'proposed_finish': proposed,
        'modelled_increment_calendar_days': net, 'contract_overrun_calendar_days': selected.get('contract_overrun_calendar_days'),
        'entitlement_status': 'not_determined', 'approved_extension_calendar_days': None,
        'internal_review_status': case.status,
        'statement': 'Internal review of a technical recommendation does not award a contractual extension or change the baseline.',
        'warnings': warnings}


def _validate_case(case):
    if len(case.event_ids) != len(set(case.event_ids)):
        raise ValidationError('Select each event once.')
    selected_events = {row['id']: row for row in _events(case)}
    scope = _ids(case.baseline)
    scenario_ids = [row['id'] for row in case.scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValidationError('Each recovery alternative needs its own identifier.')
    for changes in [case.changes] + [row['changes'] for row in case.scenarios]:
        for change in changes:
            event = selected_events.get(change['event_id'])
            activity_id = change.get('activity_id', change.get('successor_id'))
            if not event or activity_id not in event['activity_ids'] or activity_id not in scope:
                raise ValidationError('Every proposed change must link to a selected event and one of its exact baseline activities.')
            if change.get('predecessor_id') is not None and change['predecessor_id'] not in scope:
                raise ValidationError('Relationship endpoints must belong to this frozen baseline.')
    selected = case.recommendation.get('selected_scenario_id')
    if selected and selected not in scenario_ids:
        raise ValidationError('Select an existing recovery alternative for the recommendation.')


def _review_ready(case):
    if not case.current_run_id or case.current_run.case_id != case.pk:
        _conflict('Calculate the current case before review.', 'delay_calculation_required')
    if not case.changes:
        raise ValidationError('Record an evidence-linked impact change before review.')
    if (case.recommendation or {}).get('requested_extension_calendar_days', 0):
        fields = ('contract_clause_reference', 'notice_reference', 'causation_assessment',
                  'concurrency_assessment', 'mitigation_assessment', 'basis')
        missing = [key for key in fields if not case.recommendation.get(key, '').strip()]
        if missing:
            raise ValidationError({key: 'Required to support a time-extension recommendation.' for key in missing})


def _can_review(case, actor):
    return bool(case.status == 'submitted' and can_final_approve_defaults(actor, case.project)
        and actor.pk not in {case.created_by_id, case.edited_by_id, case.submitted_by_id}
        and not PlanningAuditEvent.objects.filter(project=case.project, entity_type='DelayAnalysisCase',
            entity_id=str(case.pk), action='delay.save_case', actor=actor).exists())


def _case_summary(case, actor):
    writable = _write(case.project, actor)
    return {key: getattr(case, key) for key in ('id', 'name', 'revision', 'status', 'baseline_id',
        'reference_report_id', 'current_run_id', 'supersedes_id', 'created_by_id', 'edited_by_id',
        'submitted_by_id', 'reviewed_by_id', 'reviewed_at')} | {'permissions': {
            'can_edit': writable and case.status in {'draft', 'calculated'},
            'can_calculate': writable and case.status in {'draft', 'calculated'},
            'can_submit': writable and case.status == 'calculated', 'can_approve': _can_review(case, actor),
            'can_return': case.status == 'submitted' and (writable or can_final_approve_defaults(actor, case.project)),
            'can_revise': writable and case.status in {'approved', 'rejected'}, 'can_export': bool(case.current_run_id)}}


def _case_detail(case, actor):
    row = _case_summary(case, actor)
    row.update(event_ids=case.event_ids, changes=case.changes, scenarios=case.scenarios,
               recommendation=case.recommendation, reason=case.reason, source_stale=False, issues=[])
    run = case.current_run
    if case.status in {'approved', 'rejected'} and run:
        source, fingerprint = run.input_snapshot, run.fingerprint
    else:
        try:
            source, fingerprint = _source(case)
        except (ScheduleApprovalError, ValidationError, Http404) as exc:
            source, fingerprint = (run.input_snapshot if run else {}), None
            row['issues'].append({'code': 'delay_source_unavailable', 'message': str(exc)})
        row['source_stale'] = bool(run and fingerprint != run.fingerprint)
    reference = source.get('reference') or {}
    row['issues'].extend(_evidence_issues(source))
    row.update(source_fingerprint=fingerprint,
        reference_observations=reference.get('observations', []),
        reference_relationships=(reference.get('baseline') or {}).get('relationships', []),
        reference_data_date=reference.get('data_date'),
        reference_report_revision=reference.get('report_revision'),
        run={'id': run.pk, 'fingerprint': run.fingerprint, 'created_at': run.created_at,
             'result': deepcopy(run.result), 'events': deepcopy(run.input_snapshot.get('events', []))} if run else None)
    if row['source_stale']:
        row['issues'].append({'code': 'delay_source_stale', 'message': 'Case inputs or source events changed. Calculate and review a new run.'})
        row['permissions'].update(can_submit=False, can_approve=False)
    if reference.get('later_correction_ids'):
        row['issues'].append({'code': 'reference_corrected_later',
            'message': 'A later published correction exists for the selected historical report. Review whether this is the appropriate reference date and revision.'})
    if run:
        row['recommendation_assessment'] = _recommendation(case, run.result)
    return row


def delay_state(project, actor, *, baseline_id=None, case_id=None):
    case = None
    if case_id:
        case = get_object_or_404(DelayAnalysisCase.objects.select_related('project', 'baseline', 'reference_report', 'current_run'),
                                pk=case_id, project=project)
        if baseline_id and int(baseline_id) != case.baseline_id:
            raise ValidationError('The selected case belongs to another baseline.')
        baseline_id = case.baseline_id
    baselines = list(ScheduleBaseline.objects.filter(schedule__project=project, schedule__is_deleted=False,
        is_deleted=False, approved_at__isnull=False, approved_by__isnull=False).order_by('-approved_at', '-pk'))
    baseline = _baseline(project, baseline_id) if baseline_id else next((row for row in baselines
        if row.source_version_id == project.master_schedule_version_id), baselines[0] if baselines else None)
    cases = list(DelayAnalysisCase.objects.filter(project=project, baseline=baseline).select_related('project')) if baseline else []
    if not case and cases:
        case = cases[0]
    events = list(DelayEvent.objects.filter(project=project, baseline=baseline).select_related('project', 'baseline', 'governance_item', 'risk')) if baseline else []
    result = {'baseline': {'id': baseline.pk, 'name': baseline.name, 'version_id': baseline.source_version_id} if baseline else None,
        'baselines': [{'id': row.pk, 'name': row.name, 'version_id': row.source_version_id} for row in baselines],
        'published_reports': [{'id': row.pk, 'baseline_id': row.baseline_id,
            'name': row.period_snapshot.get('name'), 'data_date': row.period_snapshot.get('data_date'),
            'published_at': row.published_at, 'revision': row.revision}
            for row in OperationalControlReport.objects.filter(project=project, baseline=baseline,
                status='published', published_at__isnull=False, published_by__isnull=False).order_by('-published_at', '-pk')] if baseline else [],
        'activities': [{key: row.get(key) for key in ('id', 'external_id', 'name', 'activity_type', 'planned_start', 'planned_finish')}
            for row in baseline.snapshot.get('activities', [])] if baseline else [],
        'events': [{key: getattr(row, key) for key in ('id', 'revision', 'title', 'description', 'start_date', 'end_date',
                'status', 'activity_ids', 'evidence', 'governance_item_id', 'risk_id')} |
            {'permissions': {'can_edit': _write(project, actor)}} for row in events],
        'linked_items': {'governance': list(GovernanceItem.objects.filter(version_id=baseline.source_version_id,
            is_deleted=False).values('id', 'title', 'item_type', 'activity_id')),
            'risks': list(PlanningRiskRecord.objects.filter(version_id=baseline.source_version_id).values('id', 'title'))} if baseline else {'governance': [], 'risks': []},
        'cases': [_case_summary(row, actor) for row in cases], 'case': _case_detail(case, actor) if case else None,
        'permissions': {'can_write': _write(project, actor), 'can_approve': can_final_approve_defaults(actor, project)},
        'current_user_id': actor.pk, 'issues': [] if baseline else [{'code': 'baseline_required',
            'message': 'Publish a baseline and an operational report before analysing delay and recovery.'}],
        'method_boundary': 'Forward remaining-work sensitivity from a published report. Technical impact is distinct from contractual entitlement.'}
    return _safe(result)


def _save_event(project, actor, data, *, create):
    if create:
        _required(data, 'baseline_id', 'title', 'activity_ids', 'evidence')
        baseline = _baseline(project, data['baseline_id'])
        item = DelayEvent(project=project, baseline=baseline, created_by=actor, updated_by=actor)
        before = {}
    else:
        _required(data, 'event_id', 'revision', 'reason')
        item = get_object_or_404(DelayEvent.objects.select_for_update(), pk=data['event_id'], project=project)
        if item.revision != data['revision']:
            _conflict('This event changed. Refresh before editing.', 'delay_event_revision_stale')
        if 'baseline_id' in data and data['baseline_id'] != item.baseline_id:
            raise ValidationError('Events cannot move between baselines.')
        if not data['reason'].strip():
            raise ValidationError('Record why the event is being changed.')
        before = _event_snapshot(item, validate=False)
        item.revision += 1
    for key in ('title', 'description', 'start_date', 'end_date', 'status', 'activity_ids', 'evidence', 'governance_item_id', 'risk_id'):
        if key in data:
            setattr(item, key, _safe(data[key]) if key == 'evidence' else data[key])
    if item.start_date and item.end_date and item.end_date < item.start_date:
        raise ValidationError('Event end cannot precede its start.')
    if len(item.activity_ids) != len(set(item.activity_ids)) or not set(item.activity_ids) <= _ids(item.baseline):
        raise ValidationError('Select distinct activities from this frozen baseline.')
    _evidence(project, item.evidence)
    if item.governance_item_id:
        source = get_object_or_404(GovernanceItem, pk=item.governance_item_id,
                                   version_id=item.baseline.source_version_id, is_deleted=False)
        if source.activity_id and source.activity_id not in item.activity_ids:
            raise ValidationError('Include the linked governance activity in this event scope.')
    if item.risk_id:
        get_object_or_404(PlanningRiskRecord, pk=item.risk_id, version_id=item.baseline.source_version_id)
    item.updated_by = actor
    item.save()
    record_event(project=project, actor=actor, action='delay.' + data['action'], entity=item,
        before=before, after=_event_snapshot(item), metadata={'reason': data.get('reason', '')})
    return delay_state(project, actor, baseline_id=item.baseline_id)


@transaction.atomic
def delay_command(project, actor, data):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    action = data['action']
    authority_action = action in {'approve_case', 'reject_case', 'return_case'}
    if not getattr(actor, 'is_active', False) or not (_write(project, actor)
            or authority_action and can_final_approve_defaults(actor, project)):
        raise PermissionDenied('You cannot change delay analysis for this project.')
    if action in {'create_event', 'update_event'}:
        return _save_event(project, actor, data, create=action == 'create_event')
    before = {}
    if action == 'create_case':
        _required(data, 'baseline_id', 'reference_report_id', 'name', 'event_ids')
        baseline = _baseline(project, data['baseline_id'])
        report = _report(project, baseline, data['reference_report_id'])
        case = DelayAnalysisCase(project=project, baseline=baseline, reference_report=report,
            name=data['name'], event_ids=data['event_ids'], created_by=actor, edited_by=actor)
        _validate_case(case)
        case.save()
    else:
        _required(data, 'case_id')
        case = get_object_or_404(DelayAnalysisCase.objects.select_for_update(), pk=data['case_id'], project=project)
        before = {'revision': case.revision, 'status': case.status, 'current_run_id': case.current_run_id}
        if action == 'revise_case':
            if case.status not in {'approved', 'rejected'} or not data.get('reason', '').strip():
                raise ValidationError('Select a reviewed case and state the reason for a new revision.')
            case = DelayAnalysisCase.objects.create(project=project, baseline=case.baseline,
                reference_report=case.reference_report, supersedes=case, name=case.name,
                event_ids=case.event_ids, changes=case.changes, scenarios=case.scenarios,
                recommendation=case.recommendation, reason=data['reason'], created_by=actor, edited_by=actor)
        else:
            _required(data, 'revision')
            if data['revision'] != case.revision:
                _conflict('This case changed. Refresh before saving or reviewing.', 'delay_case_revision_stale')
            if case.status in {'approved', 'rejected'}:
                _conflict('Reviewed cases are immutable. Create a revision.')
            if action == 'save_case':
                if case.status not in {'draft', 'calculated'}:
                    _conflict('Return the submitted case before editing.')
                for key in ('event_ids', 'changes', 'scenarios', 'recommendation', 'name'):
                    if key in data:
                        setattr(case, key, _safe(data[key]))
                _validate_case(case)
                case.status, case.current_run, case.edited_by = 'draft', None, actor
            elif action == 'calculate_case':
                if case.status not in {'draft', 'calculated'}:
                    _conflict('Only a draft case can be recalculated.')
                from .delay_analysis import analyze_delay_case
                _lock_sources(case)
                _validate_case(case)
                source, fingerprint = _source(case)
                result = analyze_delay_case(source['reference'], source['events'], case.changes, case.scenarios)
                result.setdefault('issues', []).extend(_evidence_issues(source))
                if source['reference'].get('later_correction_ids'):
                    result.setdefault('issues', []).append({'code': 'reference_corrected_later', 'severity': 'warning',
                        'message': 'A later published correction exists. This run uses the explicitly selected historical reference.'})
                case.current_run = DelayAnalysisRun.objects.create(case=case, case_revision=case.revision,
                    fingerprint=fingerprint, input_snapshot=source, result=_safe(result), created_by=actor)
                case.status = 'calculated'
            elif action == 'return_case':
                if case.status != 'submitted' or not data.get('reason', '').strip():
                    raise ValidationError('A submitted case and a return reason are required.')
                case.status, case.reason, case.submission_fingerprint = 'calculated', data['reason'], ''
            elif action in {'submit_case', 'approve_case', 'reject_case'}:
                _required(data, 'source_fingerprint', 'reason')
                if not data['reason'].strip():
                    raise ValidationError('Record the submission or review reason.')
                if action == 'reject_case':
                    if not _can_review(case, actor):
                        raise PermissionDenied('An independent project authority must decide this case.')
                    case.status, case.reviewed_by, case.reviewed_at = 'rejected', actor, timezone.now()
                else:
                    _review_ready(case)
                    _lock_sources(case)
                    _, fingerprint = _source(case)
                    if data['source_fingerprint'] != fingerprint or case.current_run.fingerprint != fingerprint:
                        _conflict('The event evidence or case inputs changed. Calculate and review a new run.', 'delay_case_sources_changed')
                    if action == 'submit_case':
                        if case.status != 'calculated':
                            _conflict('Calculate the case before submitting.')
                        case.status, case.submitted_by, case.submitted_at = 'submitted', actor, timezone.now()
                        case.submission_fingerprint = fingerprint
                    else:
                        if not _can_review(case, actor):
                            raise PermissionDenied('An independent project authority must review this recommendation.')
                        if case.submission_fingerprint != fingerprint:
                            _conflict('The submitted run changed. Return the case for another review.')
                        case.status, case.reviewed_by, case.reviewed_at = 'approved', actor, timezone.now()
                case.reason = data['reason']
            else:
                raise ValidationError('Unsupported delay analysis command.')
            case.revision += 1
            case.save()
    record_event(project=project, actor=actor, action='delay.' + action, entity=case, before=before,
        after={'revision': case.revision, 'status': case.status, 'current_run_id': case.current_run_id,
               'inputs_fingerprint': canonical_fingerprint({'events': case.event_ids, 'changes': case.changes,
                   'scenarios': case.scenarios, 'recommendation': case.recommendation})}, metadata={'reason': data.get('reason', '')})
    return delay_state(project, actor, baseline_id=case.baseline_id, case_id=case.pk)
