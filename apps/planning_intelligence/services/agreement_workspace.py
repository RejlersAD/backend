"""One sourced agreement draft shared by the eight project workspaces.

Extraction is immutable. Acceptance records a deliberate selection and only
fills empty operational fields; it never changes an existing approved plan,
establishes a cost budget, or invents dates for relative contractual events.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from django.db import transaction
from django.db.models import Max
from django.contrib.auth import get_user_model
from django.utils import timezone

from ..models import PlanningProject
from .audit import record_event
from .evidence_graph import EvidenceError, _require_write


TABS = ('overview', 'schedule', 'commercials', 'milestones', 'risks', 'estimates', 'documents', 'activity')
FIELDS = {
    'overview': {'project_name', 'client', 'contractor', 'contract_reference', 'scope_summary'},
    'schedule': {'date_constraint', 'duration_requirement', 'review_window'},
    'commercials': {'contract_value', 'payment_term', 'performance_guarantee', 'delay_damages', 'warranty'},
    'milestones': {'milestone'}, 'risks': {'risk'},
    'estimates': {'estimate_requirement', 'cost_item'},
    'documents': {'deliverable', 'document_requirement'},
}
SINGLETONS = {'project_name', 'client', 'contractor', 'contract_reference', 'scope_summary', 'contract_value'}
ENGINE_VERSION = 'agreement-workspace-v1'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _model():
    from ..agreement_models import AgreementWorkspace
    return AgreementWorkspace


def _error(message, code='agreement_workspace_conflict', status=409):
    raise EvidenceError(message, code, status)


def _key(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').casefold()).strip()


def _source_state(files):
    return [{'file_id': file.pk, 'filename': file.original_filename, 'storage_name': file.file.name,
             'category': file.category, 'size_bytes': file.size_bytes, 'updated_at': file.updated_at.isoformat()}
            for file in sorted(files, key=lambda item: item.pk)]


def source_fingerprint(project, file_ids=None):
    files = project.files.filter(is_deleted=False)
    if file_ids is not None:
        files = files.filter(pk__in=file_ids)
    return _hash(_source_state(files))


def _verify_manifest(project, manifest):
    """Re-read original bytes; a same-path storage replacement is a conflict."""
    if not isinstance(manifest, list) or not manifest:
        _error('Upload an agreement before building a project workspace.', 'agreement_sources_missing', 400)
    ids = [item.get('file_id') for item in manifest if isinstance(item, dict)]
    if len(ids) != len(manifest) or len(set(ids)) != len(ids):
        _error('The agreement source manifest is invalid.', 'agreement_sources_changed')
    files = {file.pk: file for file in project.files.filter(pk__in=ids, is_deleted=False)}
    for item in manifest:
        file = files.get(item['file_id'])
        if file is None or file.file.name != item.get('storage_name'):
            _error('An agreement source was removed or replaced. Analyze the current upload again.', 'agreement_sources_changed')
        try:
            digest = hashlib.sha256()
            with file.file.storage.open(file.file.name, 'rb') as stream:
                for chunk in stream.chunks():
                    digest.update(chunk)
            valid = digest.hexdigest() == item.get('sha256')
        except Exception:
            valid = False
        if not valid:
            _error('An original agreement changed or is unavailable. No project inputs were accepted.', 'agreement_sources_changed')
    return list(files.values())


def workspace_is_stale(workspace):
    ids = workspace.analysis_metadata.get('source_file_ids') or [item.get('file_id') for item in workspace.source_manifest]
    return workspace.source_fingerprint != source_fingerprint(workspace.project, ids)


def _display(value):
    if not isinstance(value, dict):
        return str(value)
    if value.get('text'):
        return str(value['text'])
    if 'amount' in value and value.get('currency'):
        return f"{value['currency']} {value['amount']}"
    chunks = []
    for key, item in value.items():
        if item is None or item == '' or key in {'present_in_upload'}:
            continue
        label = key.replace('_', ' ').capitalize()
        chunks.append(f'{label}: {item}' if not isinstance(item, (dict, list)) else f'{label}: {_json(item)}')
    return ' · '.join(chunks)


def _candidate_group(candidate):
    field, value = candidate['field'], candidate['value']
    if field in SINGLETONS:
        return candidate['tab'], field
    # Starting points and completion events are contractual meaning. Do not
    # collapse "eight months from commencement" into "28 weeks from award".
    if field in {'milestone', 'date_constraint', 'duration_requirement', 'review_window'}:
        return candidate['tab'], field, _key(value.get('name') or value.get('event')), _key(value.get('anchor'))
    return candidate['tab'], field, _key(candidate.get('entity_key') or value.get('name') or value.get('label') or candidate['label'])


def _value_key(candidate):
    value = deepcopy(candidate['value'])
    # Decimal presentation is not a commercial conflict (USD 100 = USD 100.00).
    if candidate['field'] in {'contract_value', 'cost_item'}:
        amount = _decimal(value.get('amount'))
        if amount is not None:
            value['amount'] = format(amount.normalize(), 'f')
    return _json(value)


def _supported(candidate, documents):
    sources = candidate.get('sources')
    if candidate.get('basis') != 'document_fact' or not isinstance(sources, list) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict):
            return False
        document = documents.get(source.get('file_id'))
        if (not document or source.get('quote_verified') is not True or not source.get('quote')
                or source.get('sha256') != document.get('sha256')
                or source.get('text_sha256') != document.get('text_sha256')):
            return False
    return True


def _normalize(candidates, manifest):
    if not isinstance(candidates, list) or len(candidates) > 10000:
        _error('The agreement analysis returned an invalid candidate list.', 'agreement_analysis_invalid', 400)
    documents = {item['file_id']: item for item in manifest}
    result, seen = [], set()
    for source in candidates:
        if not isinstance(source, dict) or source.get('field') not in FIELDS.get(source.get('tab'), set()):
            _error('The agreement analysis returned an unsupported field.', 'agreement_analysis_invalid', 400)
        candidate = deepcopy(source)
        if not isinstance(candidate.get('value'), dict) or not candidate.get('label'):
            _error('An extracted agreement value is incomplete.', 'agreement_analysis_invalid', 400)
        candidate['id'] = str(candidate.get('id') or _hash(candidate))
        if candidate['id'] in seen:
            _error('The agreement analysis returned duplicate candidate identifiers.', 'agreement_analysis_invalid', 400)
        seen.add(candidate['id'])
        candidate['display_value'] = _display(candidate['value'])
        # Risk assessments are proposals even when their underlying obligation
        # is quoted. No inferred probability, impact, owner or variation exists.
        if candidate['field'] == 'risk':
            candidate['basis'] = 'ai_proposal'
        candidate['status'] = 'supported' if _supported(candidate, documents) else 'proposed'
        result.append(candidate)
    groups = defaultdict(list)
    for candidate in result:
        if candidate['status'] == 'supported':
            groups[_candidate_group(candidate)].append(candidate)
    for rows in groups.values():
        if len({_value_key(row) for row in rows}) > 1:
            for row in rows:
                row['status'] = 'conflict'
    return result


def _exceptions(candidates, coverage, warnings):
    result = []
    conflicts = defaultdict(list)
    for row in candidates:
        if row['status'] == 'conflict':
            conflicts[_candidate_group(row)].append(row)
    for key, rows in conflicts.items():
        result.append({'code': 'source_conflicts', 'key': _hash(key), 'label': f"Choose {rows[0]['label']}",
                       'count': len(rows), 'fact_ids': [row['id'] for row in rows],
                       'details': 'Select the applicable source value and record why it applies. Other source statements remain available.'})
    for status, code, label in [('proposed', 'proposals', 'Suggestions requiring a decision')]:
        rows = [row for row in candidates if row['status'] == status]
        if rows:
            result.append({'code': code, 'label': label, 'count': len(rows), 'fact_ids': [row['id'] for row in rows],
                           'details': 'These values remain draft inputs.'})
    relative = [row for row in candidates if row['field'] == 'milestone' and not row['value'].get('date')]
    if relative:
        result.append({'code': 'milestone_anchors', 'label': 'Relative milestones need confirmed starting points',
                       'count': len(relative), 'fact_ids': [row['id'] for row in relative],
                       'details': 'The contractual offsets are retained. No calendar dates have been assumed.'})
    timings = [row for row in candidates if row['field'] in {'milestone', 'date_constraint', 'duration_requirement'}]
    anchors = {_key(row['value'].get('anchor')) for row in timings if row['value'].get('anchor')}
    if len(anchors) > 1:
        result.append({'code': 'timing_basis', 'label': 'Confirm the contractual timing basis',
                       'count': 1, 'fact_ids': [row['id'] for row in timings],
                       'details': 'The source uses different events or starting points. They have not been treated as interchangeable.'})
    deliverables = [row for row in candidates if row['field'] == 'deliverable']
    if deliverables:
        result.append({'code': 'schedule_inputs', 'label': 'Complete schedule calculation inputs', 'count': 1,
                       'fact_ids': [row['id'] for row in deliverables],
                       'details': 'Confirm activity durations, working calendars and dependencies before CPM calculation.'})
    if not any(row['field'] == 'contract_value' for row in candidates):
        result.append({'code': 'missing_contract_value', 'label': 'Contract value is not established', 'count': 1,
                       'fact_ids': [], 'details': 'No supported contract amount was found in the analyzed material.'})
    for warning in warnings or []:
        if isinstance(warning, dict):
            result.append({'code': warning.get('code', 'analysis_coverage'),
                           'label': warning.get('message') or warning.get('label') or 'Review analysis coverage',
                           'count': 1, 'fact_ids': [], 'details': ''})
    if not candidates:
        result.append({'code': 'no_supported_facts', 'label': 'No project inputs were extracted', 'count': 1,
                       'fact_ids': [], 'details': 'Check the document and analysis settings, then analyze again.'})
    return result


def _wbs(candidates):
    stages = {}
    for row in candidates:
        if row['field'] != 'deliverable' or row['status'] == 'conflict':
            continue
        value = row['value']
        stage = value.get('stage') or 'Scope deliverables'
        discipline = value.get('discipline') or 'Unassigned discipline'
        stages.setdefault(stage, {}).setdefault(discipline, []).append({
            'id': row['id'], 'name': value.get('name') or row['label'], 'fact_ids': [row['id']],
            'status': row['status'], 'duration': None, 'dependencies': None,
        })
    return [{'name': stage, 'basis': 'ai_proposal', 'disciplines': [
        {'name': discipline, 'deliverables': rows} for discipline, rows in disciplines.items()
    ]} for stage, disciplines in stages.items()]


def build_projection(candidates, manifest, *, accepted_ids=(), activity=()):
    accepted_ids = set(accepted_ids)
    resolved = {_candidate_group(row) for row in candidates if row['id'] in accepted_ids}
    projection = {tab: {'items': []} for tab in TABS}
    for original in candidates:
        row = deepcopy(original)
        if row['id'] in accepted_ids:
            row['status'] = 'accepted'
        elif row['status'] == 'conflict' and _candidate_group(row) in resolved:
            row['status'] = 'not_selected'
        destinations = {row['tab']}
        if row['field'] == 'contract_value':
            destinations.add('overview')
        if row['field'] == 'date_constraint':
            destinations.update({'overview', 'milestones'})
        if row['field'] == 'deliverable':
            destinations.add('schedule')
        if row['field'] == 'milestone':
            destinations.add('schedule')
        for tab in destinations:
            projection[tab]['items'].append(deepcopy(row))
    projection['schedule'].update(wbs=_wbs([dict(row, status='accepted' if row['id'] in accepted_ids else row['status'])
                                         for row in candidates]), calculation_ready=False,
                                  calculation_message='Activity durations, calendars and dependencies must be confirmed before calculating CPM.')
    projection['commercials']['budget_established'] = False
    projection['commercials']['note'] = 'Contract value and payment terms are separate from the internal control budget and actual expenditure.'
    projection['estimates']['note'] = 'An estimate requirement is not a completed or approved estimate.'
    projection['risks']['note'] = 'Risk suggestions require assessment and ownership. No approved changes are created.'
    projection['documents']['uploads'] = deepcopy(manifest)
    projection['activity']['items'] = deepcopy(list(activity))
    return projection


def serialize_workspace(workspace):
    candidates = workspace.candidates or []
    accepted = set(workspace.accepted_fact_ids or [])
    return {'id': str(workspace.pk), 'project_id': workspace.project_id, 'version': workspace.version,
            'revision': workspace.revision, 'status': workspace.status, 'stale': workspace_is_stale(workspace),
            'projection': workspace.projection, 'exceptions': workspace.exceptions,
            'source_manifest': workspace.source_manifest, 'materialization': workspace.materialization,
            'analysis_metadata': workspace.analysis_metadata,
            'counts': {'document_facts': sum(row.get('basis') == 'document_fact' for row in candidates),
                       'supported': sum(row.get('status') == 'supported' and row['id'] not in accepted for row in candidates),
                       'proposals': sum(row.get('status') == 'proposed' for row in candidates),
                       'accepted': len(accepted), 'exceptions': len(workspace.exceptions)},
            'created_at': workspace.created_at.isoformat(),
            'accepted_at': workspace.accepted_at.isoformat() if workspace.accepted_at else None}


def agreement_workspace_summary(project, actor=None):
    workspace = _model().objects.filter(project=project, is_deleted=False).order_by('-version').first()
    return serialize_workspace(workspace) if workspace else None


def _persist_parsed_sources(project, manifest, parsed_files):
    """Reuse this analysis's OCR in normal document/evidence screens."""
    from ..intelligence_models import DocumentProfile
    if not parsed_files:
        return manifest
    documents = {item['file_id']: item for item in manifest}
    seen = set()
    for parsed in parsed_files:
        file_id = parsed.get('file_id')
        document = documents.get(file_id)
        text = parsed.get('text')
        if (document is None or file_id in seen or not isinstance(text, str)
                or hashlib.sha256(text.encode('utf-8')).hexdigest() != document['text_sha256']):
            _error('Parsed agreement text does not match its source manifest.', 'agreement_analysis_invalid', 400)
        seen.add(file_id)
        file = project.files.select_for_update().get(pk=file_id, is_deleted=False)
        confidence = parsed.get('confidence')
        confidence = float(confidence) if isinstance(confidence, (int, float)) and math.isfinite(confidence) else 0.0
        file.extracted_text = text
        file.confidence_score = max(0.0, min(confidence, 1.0))
        file.parse_status = 'done' if text.strip() else 'failed'
        file.parse_error = '' if text.strip() else 'No readable agreement text was recovered. Review extraction coverage.'
        file.save(update_fields=['extracted_text', 'confidence_score', 'parse_status', 'parse_error', 'updated_at'])
        coverage = parsed.get('coverage') if isinstance(parsed.get('coverage'), dict) else {}
        DocumentProfile.objects.update_or_create(file=file, defaults={
            'declared_category': file.category, 'checksum_sha256': document['sha256'],
            'page_count': document.get('page_count') or 0, 'word_count': len(text.split()),
            'extraction_method': 'agreement_workspace', 'extraction_coverage': coverage,
        })
        document['updated_at'] = file.updated_at.isoformat()
    return manifest


def analyze_agreement_workspace(project, actor, *, file_ids=None, progress=None, job=None):
    from .agreement_extraction import extract_agreement_workspace
    _require_write(project, actor)
    if job:
        previous = _model().objects.filter(project=project, job=job, is_deleted=False).first()
        if previous:
            return previous
    files = list(project.files.filter(is_deleted=False, **({'pk__in': file_ids} if file_ids is not None else {})).order_by('pk'))
    if not files or file_ids is not None and {file.pk for file in files} != set(file_ids):
        _error('Select uploaded documents belonging to this project.', 'agreement_sources_missing', 400)
    fingerprint = _hash(_source_state(files))
    if job and (job.request_data or {}).get('source_fingerprint') not in {None, fingerprint}:
        _error('The selected documents changed while analysis was queued. Start a new analysis.', 'agreement_sources_changed')
    result = extract_agreement_workspace(project, files, user=actor, progress=progress)
    manifest = result.get('document_manifest') or []
    manifested_ids = {item.get('file_id') for item in manifest}
    selected_ids = {file.pk for file in files}
    omitted_ids = selected_ids - manifested_ids
    reported_omissions = {item.get('file_id') for item in result.get('warnings') or []
                          if isinstance(item, dict) and item.get('code') in {'source_unreadable', 'file_limit'}}
    if not manifested_ids.issubset(selected_ids) or not omitted_ids.issubset(reported_omissions):
        _error('The agreement analysis did not account for every selected source.', 'agreement_analysis_invalid', 400)
    candidates = _normalize(result.get('candidates'), manifest)
    exceptions = _exceptions(candidates, result.get('coverage') or {}, result.get('warnings'))
    with transaction.atomic():
        project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
        actor = get_user_model().objects.get(pk=actor.pk)
        _require_write(project, actor)
        if job:
            previous = _model().objects.filter(project=project, job=job, is_deleted=False).first()
            if previous:
                return previous
        if source_fingerprint(project, [file.pk for file in files]) != fingerprint:
            _error('The uploaded documents changed during analysis. Analyze the current files again.', 'agreement_sources_changed')
        _verify_manifest(project, manifest)
        manifest = _persist_parsed_sources(project, manifest, result.get('parsed_files'))
        fingerprint = source_fingerprint(project, [file.pk for file in files])
        version = (_model().objects.filter(project=project).aggregate(value=Max('version'))['value'] or 0) + 1
        activity = [{'id': f'analysis-{version}', 'label': 'Agreement analyzed', 'basis': 'recorded',
                     'display_value': f'{len(candidates)} extracted inputs; {len(exceptions)} exception groups',
                     'created_at': timezone.now().isoformat(), 'actor_id': actor.pk}]
        workspace = _model().objects.create(project=project, version=version, revision=1,
            status='partial' if result.get('warnings') else 'draft', job=job,
            source_fingerprint=fingerprint, source_manifest=manifest, candidates=candidates, exceptions=exceptions,
            projection=build_projection(candidates, manifest, activity=activity), requested_by=actor,
            analysis_metadata={'engine_version': ENGINE_VERSION, 'job_id': job.pk if job else None,
                               'source_file_ids': [file.pk for file in files],
                               'coverage': result.get('coverage') or {}, 'warnings': result.get('warnings') or []})
        record_event(project=project, actor=actor, action='agreement.analyzed', entity=workspace,
                     after={'version': version, 'candidate_count': len(candidates), 'exception_groups': len(exceptions)},
                     metadata={'source_fingerprint': fingerprint, 'file_ids': [file.pk for file in files]})
    return workspace


def _iso(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _decimal(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _reference(source):
    return {'file_id': source['file_id'], 'filename': source.get('filename'),
            'checksum_sha256': source['sha256'], 'extracted_text_sha256': source['text_sha256'],
            'locator': {'page': source.get('page'), 'character_start': source.get('char_start'),
                        'character_end': source.get('char_end')},
            'excerpt': source['quote'], 'verbatim': source['quote'], 'quote_verified': True}


def _materialize(project, workspace, accepted, actor):
    from apps.core.project_models import Project, ProjectMilestone
    from apps.rbac.action_policy import module_action_allowed
    from ..schedule_models import ScheduleVersion
    changes, retained, milestones = [], [], []
    core_write = module_action_allowed(actor, 'project_control', 'update')
    enterprise = (Project.objects.select_for_update().get(pk=project.enterprise_project_id)
                  if project.enterprise_project_id and core_write else None)
    protected = ScheduleVersion.objects.filter(schedule__project=project, is_deleted=False,
                                                 status__in=['approved', 'baselined']).exists()
    changed = defaultdict(set)

    def fill(target, field, value, candidate):
        current = getattr(target, field)
        if protected:
            if str(current or '') != str(value):
                retained.append({'field': field, 'existing': str(current or ''), 'source_value': str(value),
                                 'fact_id': candidate['id'], 'reason': 'approved_schedule_preserved'})
            return
        generated_name = (field == 'name' and current == 'Agreement project' and enterprise is not None
                          and (enterprise.custom_fields or {}).get('agreement_setup') is True)
        if current is None or current == '' or (field == 'name' and current == 'Untitled Planning Project') or generated_name:
            setattr(target, field, value)
            changed[target].add(field)
            changes.append({'model': target.__class__.__name__, 'field': field, 'value': str(value), 'fact_id': candidate['id']})
        elif str(current).strip().casefold() != str(value).strip().casefold():
            retained.append({'field': field, 'existing': str(current), 'source_value': str(value), 'fact_id': candidate['id']})

    by_field = defaultdict(list)
    for candidate in accepted:
        by_field[candidate['field']].append(candidate)
    for field, planning_field, enterprise_field in [('project_name', 'name', 'name'), ('client', 'client', 'client_name'),
                                                    ('scope_summary', 'scope_summary', 'description')]:
        rows = by_field[field]
        if rows:
            candidate, text = rows[0], rows[0]['value'].get('text')
            if isinstance(text, str) and text.strip():
                fill(project, planning_field, text[:255] if planning_field in {'name', 'client'} else text, candidate)
                if enterprise:
                    fill(enterprise, enterprise_field, text[:255] if enterprise_field in {'name', 'client_name'} else text, candidate)
    if enterprise and by_field['contract_value']:
        candidate = by_field['contract_value'][0]
        amount, currency = _decimal(candidate['value'].get('amount')), candidate['value'].get('currency')
        if amount is not None and amount < Decimal('1000000000000') and re.fullmatch(r'[A-Z]{3}', str(currency or '')):
            if enterprise.contract_value is None and not protected:
                fill(enterprise, 'contract_value', amount, candidate)
                enterprise.currency = currency
                changed[enterprise].add('currency')
            elif enterprise.contract_value != amount or enterprise.currency != currency:
                retained.append({'field': 'contract_value', 'existing': f'{enterprise.currency} {enterprise.contract_value}',
                                 'source_value': f'{currency} {amount}', 'fact_id': candidate['id']})
    starts = [row for row in by_field['date_constraint'] if _key(row['value'].get('event')) in
              {'commencement', 'commencement date', 'project commencement', 'project start', 'start date'}]
    if len({row['value'].get('date') for row in starts}) == 1:
        candidate = starts[0]
        start = _iso(candidate['value'].get('date'))
        if start:
            fill(project, 'effective_date', start, candidate)
            if enterprise:
                fill(enterprise, 'start_date', start, candidate)
    if enterprise and not protected:
        for candidate in by_field['milestone']:
            value = candidate['value']
            target = _iso(value.get('date'))
            name = str(value.get('name') or candidate['label'])[:255]
            if target is None:
                continue
            existing = enterprise.milestones.filter(is_deleted=False, name__iexact=name).first()
            if existing:
                if existing.target_date != target:
                    retained.append({'field': 'milestone', 'existing': str(existing.target_date),
                                     'source_value': str(target), 'fact_id': candidate['id']})
                continue
            record = ProjectMilestone.objects.create(project=enterprise, name=name, target_date=target,
                description=f"Agreement workspace {workspace.pk}; fact {candidate['id']}. "
                            + str(value.get('acceptance_criteria') or ''))
            milestones.append({'id': record.pk, 'fact_id': candidate['id']})
    deliverables = by_field['deliverable']
    draft_created = False
    if deliverables and not project.simple_planning_state and not project.manual_work_breakdown and not project.master_schedule_version_id:
        existing_work = project.schedules.filter(is_deleted=False).exists() or project.generations.filter(is_deleted=False).exists()
        if not existing_work:
            from .simple_planning import _fingerprint, _seed_task
            tasks, disciplines, seen = [], [], set()
            for candidate in deliverables:
                value = candidate['value']
                identity = _key(value.get('name') or candidate['label'])
                if identity in seen:
                    continue
                seen.add(identity)
                discipline = _key(value.get('discipline')) or 'unassigned'
                if discipline not in {item['code'] for item in disciplines}:
                    disciplines.append({'code': discipline, 'name': value.get('discipline') or 'Unassigned discipline'})
                tasks.append(_seed_task({'id': 'AGR-' + _hash([str(workspace.pk), candidate['id']])[:32],
                    'title': value.get('name') or candidate['label'], 'discipline': discipline,
                    'phase': value.get('stage') or '', 'duration_days': None, 'duration_unit': None,
                    'depends_on': [], 'dependency_status': 'missing', 'activity_type': 'task',
                    'evidence_policy': 'document_driven', 'duration_policy': 'source_only',
                    'source_references': [_reference(source) for source in candidate['sources']],
                    'agreement_fact_id': candidate['id'], 'agreement_workspace_id': str(workspace.pk),
                    'source_missing_fields': ['duration', 'calendar', 'dependencies']}))
            project.simple_planning_state = {'state': 'review', 'revision': 1, 'method': 'agreement_workspace',
                'tasks': tasks, 'disciplines': disciplines, 'assignment_token': f'simple:{project.pk}',
                'input_fingerprint': _fingerprint(project), 'version_id': None, 'review_id': None, 'baseline_id': None,
                'warnings': [], 'agreement_workspace_id': str(workspace.pk), 'updated_by': actor.pk,
                'updated_at': timezone.now().isoformat(), 'evidence_policy': 'document_driven', 'duration_policy': 'source_only'}
            changed[project].add('simple_planning_state')
            draft_created = True
    for target, fields in changed.items():
        target.save(update_fields=sorted(fields))
    return {'fields': changes, 'retained_existing': retained, 'milestones': milestones,
            'planning_draft_created': draft_created, 'existing_schedule_preserved': not draft_created,
            'baseline_approved': False, 'budget_changed': False, 'actuals_changed': False,
            'core_fields_permitted': core_write, 'approved_schedule_preserved': protected}


@transaction.atomic
def accept_agreement_workspace(project, actor, *, workspace_id, revision, reason='', selected_fact_ids=None):
    # The upload API locks enterprise -> planning. Use the same order here so
    # an upload and acceptance cannot deadlock while filling project metadata.
    from apps.core.project_models import Project
    if project.enterprise_project_id:
        Project.objects.select_for_update().get(pk=project.enterprise_project_id, is_deleted=False)
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    actor = get_user_model().objects.get(pk=actor.pk)
    _require_write(project, actor)
    workspace = _model().objects.select_for_update().filter(pk=workspace_id, project=project, is_deleted=False).first()
    if workspace is None:
        _error('This agreement workspace does not belong to the project.', 'agreement_workspace_missing', 404)
    if project.jobs.filter(job_type='agreement_setup', status__in=['queued', 'running'], is_deleted=False).exists():
        _error('Wait for the current agreement analysis to finish.', 'agreement_analysis_running')
    # Duplicate clicks/retries cannot create duplicate milestones or drafts.
    requested_ids = selected_fact_ids or []
    if not isinstance(requested_ids, list) or not all(isinstance(item, str) for item in requested_ids):
        _error('Select document candidate identifiers to resolve a conflict.', 'agreement_selection_invalid', 400)
    if (workspace.status == 'accepted' and revision in {workspace.revision, workspace.revision - 1}
            and set(requested_ids).issubset(workspace.accepted_fact_ids)):
        return serialize_workspace(workspace)
    if workspace.revision != revision:
        _error('The agreement workspace changed. Refresh before accepting inputs.', 'agreement_revision_conflict')
    if _model().objects.filter(project=project, version__gt=workspace.version, is_deleted=False).exists():
        _error('A newer agreement analysis exists. Review the latest draft.', 'agreement_revision_conflict')
    if workspace_is_stale(workspace):
        _error('The agreement sources changed. Analyze the current files again.', 'agreement_sources_changed')
    _verify_manifest(project, workspace.source_manifest)
    already = set(workspace.accepted_fact_ids or [])
    accepted = [row for row in workspace.candidates if row.get('status') == 'supported' or row['id'] in already]
    selected = set(requested_ids)
    if selected:
        if not str(reason or '').strip():
            _error('Explain which source value applies when resolving a conflict.', 'agreement_selection_reason_required', 400)
        candidates = {row['id']: row for row in workspace.candidates}
        if any(key not in candidates or candidates[key]['status'] != 'conflict' for key in selected):
            _error('Only conflicting source-supported values can be selected.', 'agreement_selection_invalid', 400)
        groups = defaultdict(set)
        for row in accepted:
            if row['id'] in already:
                groups[_candidate_group(row)].add(_value_key(row))
        for key in selected:
            row = candidates[key]
            groups[_candidate_group(row)].add(_value_key(row))
        if any(len(values) != 1 for values in groups.values()):
            _error('Select one applicable value for each conflicting field.', 'agreement_selection_invalid', 400)
        accepted.extend(candidates[key] for key in sorted(selected) if key not in already)
    materialization = _materialize(project, workspace, accepted, actor)
    accepted_ids = [row['id'] for row in accepted]
    now = timezone.now()
    previous_status = workspace.status
    activity = list((workspace.projection.get('activity') or {}).get('items') or [])
    activity.append({'id': f'accept-{workspace.pk}-{workspace.revision + 1}', 'label': 'Supported inputs accepted', 'basis': 'recorded',
                     'display_value': f'{len(accepted_ids)} inputs accepted into the draft project workspace',
                     'created_at': now.isoformat(), 'actor_id': actor.pk, 'reason': reason})
    exceptions = deepcopy(workspace.exceptions)
    if selected:
        resolved = {_candidate_group(row) for row in accepted if row['id'] in selected}
        resolved_keys = {_hash(key) for key in resolved}
        exceptions = [row for row in exceptions if row['code'] != 'source_conflicts' or row.get('key') not in resolved_keys]
    exceptions = [row for row in exceptions if row['code'] != 'existing_values_preserved']
    if materialization['retained_existing']:
        exceptions.append({'code': 'existing_values_preserved', 'label': 'Existing project values retained',
                           'count': len(materialization['retained_existing']),
                           'fact_ids': [row['fact_id'] for row in materialization['retained_existing']],
                           'details': 'Accepted source statements are available alongside existing project settings; existing values were not overwritten.'})
    workspace.status = 'accepted'
    workspace.revision += 1
    workspace.accepted_fact_ids = accepted_ids
    workspace.accepted_by = actor
    workspace.accepted_at = now
    workspace.materialization = materialization
    workspace.exceptions = exceptions
    workspace.projection = build_projection(workspace.candidates, workspace.source_manifest, accepted_ids=accepted_ids, activity=activity)
    workspace.save(update_fields=['status', 'revision', 'accepted_fact_ids', 'accepted_by', 'accepted_at',
                                  'materialization', 'exceptions', 'projection', 'updated_at'])
    record_event(project=project, actor=actor, action='agreement.accepted', entity=workspace,
                 before={'revision': revision, 'status': previous_status},
                 after={'revision': workspace.revision, 'accepted_fact_ids': accepted_ids, 'materialization': materialization},
                 metadata={'reason': reason, 'source_fingerprint': workspace.source_fingerprint,
                           'document_manifest': workspace.source_manifest, 'selected_fact_ids': sorted(selected)})
    return serialize_workspace(workspace)
