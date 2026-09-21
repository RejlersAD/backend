"""Persistent, versioned knowledge and decisions; no implicit schedule writes.

Only explicit refresh/decision commands mutate this graph. Source assertions are
immutable values. Corrections create approved-input nodes; they never edit a
document assertion, source fragment, original upload or schedule activity.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction
from django.utils import timezone

from ..evidence_models import EvidenceDecision, EvidenceDocumentVersion, EvidenceEdge, EvidenceGraph, EvidenceNode
from ..models import PlanningProject
from .audit import record_event
from .evidence_schema import REQUIRED, RULE_VERSION, SCHEMA_VERSION, input_schema, network_issues, validate_value


class EvidenceError(ValueError):
    def __init__(self, message, code='evidence_validation', status=409):
        super().__init__(message)
        self.payload = {'error': message, 'code': code}
        self.status_code = status


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _id(*parts):
    return uuid5(NAMESPACE_URL, 'radai:evidence:' + _json(parts))


def _require_write(project, actor):
    from ..access import can_write_project
    from apps.rbac.action_policy import module_action_allowed
    if not can_write_project(actor, project) or not module_action_allowed(actor, 'planning_package', 'update'):
        raise EvidenceError('Your project permissions do not allow evidence changes.', 'evidence_write_forbidden', 403)


def source_manifest(project):
    """Read-only freshness token; original file hashes are captured at refresh."""
    return [{'id': file.pk, 'filename': file.original_filename, 'category': file.category, 'storage_name': file.file.name,
             'size': file.size_bytes, 'updated_at': file.updated_at.isoformat(), 'parse_status': file.parse_status,
             'text_sha256': hashlib.sha256((file.extracted_text or '').encode('utf-8')).hexdigest()}
            for file in project.files.filter(is_deleted=False).order_by('pk')]


def input_fingerprint(project):
    tasks = project.simple_planning_state.get('tasks') or []
    fields = ('id', 'title', 'source_activity_id', 'source_evidence', 'duration_evidence', 'duration_days', 'duration_unit',
              'activity_type', 'depends_on', 'dependency_details', 'calendar_id', 'constraint_type', 'constraint_date')
    run = project.intelligence_runs.filter(status='succeeded', is_deleted=False).order_by('-pk').first()
    # Analysis can change without a new upload. Include the actual assertions,
    # including rejected/deleted states, so bulk review updates also invalidate
    # downstream knowledge even when a caller does not touch updated_at.
    analysis = None if run is None else {
        'id': run.pk, 'engine_version': run.engine_version,
        'facts': list(run.facts.order_by('pk').values(
            'id', 'fact_type', 'key', 'value', 'source_file_id', 'source_locator',
            'source_excerpt', 'extraction_method', 'status', 'is_deleted')),
    }
    return _hash({'sources': source_manifest(project), 'schema': SCHEMA_VERSION, 'rules': RULE_VERSION,
                  'register_evidence_version': 'explicit-cells-1',
                  'analysis': analysis,
                  'tasks': [{key: row.get(key) for key in fields} for row in tasks],
                  'start': project.effective_date, 'finish': project.planned_end_date,
                  'calendar': project.calendar_overrides})


def _document(graph, file):
    text = file.extracted_text or ''
    text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    digest, integrity = '', 'unavailable'
    try:
        hasher = hashlib.sha256()
        with file.file.open('rb') as stream:
            for chunk in stream.chunks():
                hasher.update(chunk)
        digest, integrity = hasher.hexdigest(), 'verified'
    except Exception:
        # Missing storage is a visible review issue, never a fabricated hash.
        pass
    profile = getattr(file, 'document_profile', None)
    doc_id = _id(graph.project_id, file.pk, file.file.name, digest, text_hash, SCHEMA_VERSION)
    document, _ = EvidenceDocumentVersion.objects.get_or_create(pk=doc_id, defaults={
        'graph': graph, 'source_file': file, 'filename': file.original_filename,
        'storage_name': file.file.name, 'file_sha256': digest, 'text_sha256': text_hash,
        'extracted_text': text, 'integrity_status': integrity,
        'extraction_method': getattr(profile, 'extraction_method', ''),
        'coverage': deepcopy(getattr(profile, 'extraction_coverage', {})),
    })
    return document


def _source(document, reference):
    locator = deepcopy(reference.get('locator') or reference.get('source_locator') or {})
    context = reference.get('excerpt') or reference.get('source_excerpt') or ''
    excerpt = locator.get('quote') if isinstance(locator.get('quote'), str) and locator['quote'] else context
    # Recover a physical extracted-text line only when its recorded locator is
    # valid. This is not a generated source page/cell or a semantic title match.
    if not excerpt and isinstance(locator.get('line'), int):
        lines = document.extracted_text.splitlines()
        if 0 < locator['line'] <= len(lines):
            excerpt = lines[locator['line'] - 1]
    supported = False
    if excerpt and locator:
        lines = document.extracted_text.splitlines(keepends=True)
        line = locator.get('line')
        if type(locator.get('character_start')) is int and type(locator.get('character_end')) is int:
            start, end = locator['character_start'], locator['character_end']
            supported = 0 <= start < end <= len(document.extracted_text) and excerpt == document.extracted_text[start:end]
        elif type(line) is int and 0 < line <= len(lines):
            offset = sum(map(len, lines[:line - 1]))
            # Multi-line excerpts may continue from the recorded physical line.
            position = document.extracted_text.find(excerpt, offset, offset + len(lines[line - 1]) + len(excerpt))
            supported = offset <= position < offset + len(lines[line - 1])
        elif type(locator.get('start')) is int and type(locator.get('end')) is int:
            start, end = locator['start'], locator['end']
            supported = 0 <= start < end <= len(document.extracted_text) and excerpt in document.extracted_text[start:end]
    return {'file_id': document.source_file_id, 'filename': document.filename,
            'document_version': str(document.pk), 'sha256': document.file_sha256 or None,
            'text_sha256': document.text_sha256, 'locator': locator, 'excerpt': excerpt,
            **({'context_excerpt': context} if context and context != excerpt else {}),
            'verbatim': excerpt, 'quote_verified': supported,
            'extraction_method': reference.get('extraction_method') or document.extraction_method,
            'extraction_run_id': reference.get('extraction_run_id') or document.extraction_run_id or None}


class GraphBuilder:
    def __init__(self, graph, documents):
        self.graph, self.documents, self.nodes, self.edges = graph, documents, {}, {}
        # A resumed analysis has a new run ID, but a recorded review still
        # applies to the identical source occurrence. Index all current matches
        # so duplicate candidates cannot silently select one accepted record.
        self._current_assertions = defaultdict(list)
        self._decided_fact_ids = {
            fact_id for pair in graph.decisions.values_list('fact_id', 'target_fact_id')
            for fact_id in pair if fact_id is not None
        }
        for existing in graph.nodes.filter(current=True, kind='fact', provenance_type='document_evidence'):
            signature = self._review_identity(existing.entity_id, existing.property, existing.value,
                                              existing.sources, existing.rule)
            if signature is not None:
                self._current_assertions[signature].append(existing)

    @staticmethod
    def _review_identity(entity, prop, value, sources, rule):
        if not sources:
            return None
        stable_sources = []
        for source in sources:
            if not isinstance(source, dict) or not all(source.get(key) for key in (
                    'document_version', 'sha256', 'text_sha256', 'locator', 'verbatim', 'quote_verified')):
                return None
            # Detector, document identity, hashes, exact locator and quote remain
            # in this comparison. Only the new analysis run's lineage differs.
            stable_sources.append({key: item for key, item in source.items() if key != 'extraction_run_id'})
        return _hash({'entity': entity, 'property': prop, 'value': value, 'sources': stable_sources,
                      'rule': rule or {}})

    def node(self, *, kind, entity, name='', prop='', value=None, sources=None, provenance='', status=None, rule=None):
        sources = sources or []
        key = _id(self.graph.project_id, SCHEMA_VERSION, RULE_VERSION, kind, entity, prop, value, sources, rule)
        error = validate_value(prop, value) if kind == 'fact' else None
        verified = bool(sources) and all(item['quote_verified'] for item in sources)
        validation = {'error': error, 'quote_verified': verified, 'schema': SCHEMA_VERSION}
        if kind == 'fact' and provenance == 'document_evidence':
            signature = self._review_identity(entity, prop, value, sources, rule)
            candidates = self._current_assertions.get(signature, []) if signature is not None else []
            if (len(candidates) == 1 and candidates[0].pk in self._decided_fact_ids
                    and candidates[0].validation == validation):
                # Keep the original fact, detector/run provenance and decision;
                # do not copy acceptance onto a newly extracted assertion.
                existing = candidates[0]
                self.nodes[existing.pk] = existing
                return existing
        node = EvidenceNode(id=key, graph=self.graph, kind=kind, entity_id=entity, entity_name=name[:500],
                            property=prop, value=deepcopy(value), sources=sources, provenance_type=provenance,
                            document_version_id=UUID(sources[0]['document_version']) if sources else None,
                            rule=rule or {}, status=status or ('missing' if value is None else 'detected'),
                            confidence={'extraction': {'kind': 'not_calibrated', 'score': None},
                                        'evidence_completeness': 'located' if verified else 'requires_review',
                                        'cross_source_consistency': 'not_checked', 'human_validation': 'pending'},
                            validation=validation)
        self.nodes[key] = node
        return node

    def edge(self, source, target, relationship, provenance):
        key = _id(self.graph.project_id, source.id, target.id, relationship, provenance)
        self.edges[key] = EvidenceEdge(id=key, graph=self.graph, source_id=source.id, target_id=target.id,
                                        relationship=relationship, provenance=provenance)

    def fact(self, entity, name, prop, value, references=None):
        sources = [_source(self.documents[ref['file_id']], ref) for ref in references or []
                   if ref.get('file_id') in self.documents]
        fact = self.node(kind='fact', entity=entity, name=name, prop=prop, value=value, sources=sources,
                         provenance='document_evidence' if sources else '')
        for source in sources:
            fragment = self.node(kind='evidence_fragment', entity=f"document:{source['document_version']}",
                                 name=source['filename'], value={'locator': source['locator'], 'verbatim': source['verbatim']},
                                 sources=[source], provenance='document_evidence', status='validated' if source['quote_verified'] else 'invalid')
            self.edge(fact, fragment, 'supported_by', {'type': 'deterministic_derivation', 'rule': 'exact_source_reference', 'version': RULE_VERSION})
        return fact

    def activity(self, entity, name, values, references):
        for prop, value in values.items():
            self.fact(entity, name, prop, value, references)


def _activity_values(row):
    evidence = row.get('source_evidence') or row.get('duration_evidence') or {}
    values = evidence.get('values') or {}
    duration = values.get('duration')
    if not duration and values.get('original_duration_days') is not None:
        duration = {'value': values['original_duration_days'], 'unit': values.get('duration_unit')}
    kind = values.get('is_milestone')
    # A printed boolean does not distinguish start/finish milestones.
    activity_type = 'task' if kind is False else None
    links = None
    status = row.get('dependency_status') or (evidence.get('field_status') or {}).get('predecessors')
    if status == 'explicit_none':
        links = []
    elif row.get('predecessors'):
        links = [{'predecessor_id': str(link['id']), 'type': link.get('type'),
                  'lag': link.get('lag_days'), 'lag_unit': link.get('lag_unit')} for link in row['predecessors']]
    constraints = None
    if values.get('constraint_type') == 'none':
        constraints = []
    elif values.get('constraint_type'):
        constraints = [{'type': values['constraint_type'], 'date': values.get('constraint_date')}]
    return {'identity': row.get('name') or row.get('title'), 'duration': duration, 'constraints': constraints,
            'start_date': values.get('planned_start_date'), 'finish_date': values.get('planned_finish_date'),
            'activity_type': activity_type, 'dependencies': links, 'calendar': values.get('calendar')}


@transaction.atomic
def refresh_evidence_graph(project, actor):
    _require_write(project, actor)
    project = PlanningProject.objects.select_for_update().get(pk=project.pk)
    graph, _ = EvidenceGraph.objects.get_or_create(project=project, defaults={'schema_version': SCHEMA_VERSION, 'rule_version': RULE_VERSION})
    fingerprint = input_fingerprint(project)
    # Explicit refresh also rechecks original storage bytes. Otherwise restoring
    # an unavailable upload (or replacing bytes outside RADAI) would remain an
    # idempotent no-op forever despite the displayed integrity review issue.
    documents = {file.pk: _document(graph, file) for file in project.files.filter(is_deleted=False).select_related('document_profile')}
    current_documents = set(graph.nodes.filter(current=True, kind='document').values_list('document_version_id', flat=True))
    if graph.source_fingerprint == fingerprint and current_documents == {item.pk for item in documents.values()}:
        return graph
    from .document_plan import project_document_plan
    plan = project_document_plan(project)
    builder = GraphBuilder(graph, documents)
    for document in documents.values():
        builder.node(kind='document', entity=f'document:{document.pk}', name=document.filename,
                     value={'version': str(document.pk), 'file_sha256': document.file_sha256,
                            'text_sha256': document.text_sha256}, sources=[_source(document, {})],
                     provenance='document_evidence', status=document.integrity_status)
    for row in plan.get('activities') or []:
        if (row.get('source_evidence') or {}).get('basis') == 'document_register':
            # A scope row is not an extracted schedule activity. Keep its
            # identity below; approved planning policy may create workflow
            # activities in a separate build after explicit scope review.
            continue
        builder.activity(str(row['id']), row['name'], _activity_values(row), row.get('source_references'))
    for row in plan.get('deliverables') or []:
        entity = str(row.get('id') or _id(project.pk, 'register', row))
        builder.fact(entity, row.get('title') or row.get('name') or '', 'identity', row.get('title') or row.get('name'), row.get('source_references'))
        builder.node(kind='register_scope', entity=entity, name=row.get('title') or row.get('name') or '',
                     value={'source_register': True}, provenance='deterministic_derivation',
                     rule={'name': 'explicit_register_row', 'version': 'explicit-cells-1'})
        for dimension, cell in (row.get('explicit_dimensions') or {}).items():
            if dimension not in {'discipline', 'phase', 'package', 'area'} or not cell.get('value'):
                continue
            references = deepcopy(row.get('source_references') or [])
            # No inherited or normalized labels: retain the literal cell value
            # and exact column together with the verified original row quote.
            for reference in references:
                reference['locator'] = {**(reference.get('locator') or {}),
                                        'column': cell['column'], 'column_header': cell['header']}
            value = {'name': cell['value']} if dimension in {'discipline', 'package'} else cell['value']
            builder.fact(entity, row.get('title') or row.get('name') or '', dimension, value, references)
        if not row.get('schedule_activity_ids'):
            builder.node(kind='scope_link', entity=entity, name=row.get('title') or row.get('name') or '',
                         value={'source_register': True}, provenance='deterministic_derivation',
                         rule={'name': 'register_scope_requires_explicit_activity_link', 'version': RULE_VERSION})
    # Legacy canvas entries are proposed associations, never automatically
    # merged with a new extraction entity. Reviewers explicitly link identities.
    for row in project.simple_planning_state.get('tasks') or []:
        entity = 'task:' + str(row['id'])
        evidence = row.get('source_evidence') or row.get('duration_evidence') or {}
        refs = evidence.get('source_references') or row.get('source_references') or []
        values = _activity_values(row)
        builder.activity(entity, row.get('title', ''), values, refs)
    builder.fact('project', project.name, 'project_start', project.effective_date.isoformat() if project.effective_date else None)
    builder.fact('project', project.name, 'project_finish', project.planned_end_date.isoformat() if project.planned_end_date else None)
    builder.fact('project', project.name, 'calendar', project.calendar_overrides or None)
    builder.fact('project', project.name, 'scope_complete', None)
    # Preserve all other supported intelligence assertions without allowing an
    # AI score or a previous inferred template to establish accepted knowledge.
    run = project.intelligence_runs.filter(status='succeeded', is_deleted=False).order_by('-pk').first()
    if run:
        for item in run.facts.filter(is_deleted=False).exclude(status__in=['superseded', 'rejected']).iterator():
            refs = [{'file_id': item.source_file_id, 'locator': item.source_locator, 'excerpt': item.source_excerpt,
                     'extraction_method': item.extraction_method, 'extraction_run_id': str(run.pk)}]
            builder.fact(f'assertion:{item.source_file_id}:{item.key}', str(item.key), item.fact_type, item.value, refs)
    previous_ids = set(graph.nodes.filter(current=True).values_list('id', flat=True))
    graph.nodes.filter(current=True).update(current=False)
    graph.edges.filter(current=True).update(current=False)
    EvidenceNode.objects.bulk_create(list(builder.nodes.values()), ignore_conflicts=True, batch_size=500)
    graph.nodes.filter(id__in=builder.nodes).update(current=True)
    EvidenceEdge.objects.bulk_create(list(builder.edges.values()), ignore_conflicts=True, batch_size=500)
    graph.edges.filter(id__in=builder.edges).update(current=True)
    # Planner decisions survive only while their exact input assertions remain
    # current; changed sources require explicit impact review.
    surviving = set(builder.nodes)
    pending = list(graph.edges.filter(relationship='supersedes').select_related('source', 'target'))
    while pending:
        ready = [edge for edge in pending if edge.target_id in surviving and edge.source.provenance_type == 'approved_planning_input']
        if not ready:
            break
        for edge in ready:
            graph.nodes.filter(pk=edge.source_id).update(current=True)
            graph.nodes.filter(pk=edge.target_id).update(status='superseded')
            graph.edges.filter(pk=edge.pk).update(current=True)
            surviving.add(edge.source_id)
            pending.remove(edge)
    graph.edges.filter(relationship='same_as', source__current=True, target__current=True).update(current=True)
    graph.revision += 1
    graph.source_fingerprint, graph.source_manifest = fingerprint, source_manifest(project)
    graph.schema_version, graph.rule_version, graph.built_at = SCHEMA_VERSION, RULE_VERSION, timezone.now()
    graph.save()
    record_event(project=project, actor=actor, action='evidence_graph.refreshed', entity=graph,
                 after={'revision': graph.revision, 'fact_count': len(builder.nodes)},
                 metadata={'removed_or_changed_nodes': len(previous_ids - set(builder.nodes)), 'source_fingerprint': fingerprint})
    return graph


def _issue(node, code, message, *, candidates=None, blocks=None, allowed=None):
    return {'id': f'{code}:{node.id}', 'kind': code, 'code': code, 'severity': 'error', 'status': 'open',
            'title': f'{node.entity_name or node.entity_id} · {node.property}', 'message': message,
            'field': node.property, 'entity_id': node.entity_id,
            'affected_entities': [{'id': node.entity_id, 'name': node.entity_name}],
            'candidate_fact_ids': [str(item.id) for item in candidates or [node]],
            'required_decision': 'Review the source or record an approved planning input with a reason.',
            'blocks': blocks if blocks is not None else ['calculation', 'approval', 'export'],
            'allowed_actions': allowed or (['accept', 'reject', 'correct', 'link'] if node.property == 'identity' else ['accept', 'reject', 'correct']), 'input_schema': input_schema(node.property)}


def _knowledge(graph):
    nodes = list(graph.nodes.filter(current=True, kind='fact').order_by('entity_id', 'property', 'id'))
    identity_states = defaultdict(list)
    for node in nodes:
        if node.property == 'identity' and node.status != 'superseded':
            identity_states[node.entity_id].append(node.status)
    excluded_entities = {entity for entity, states in identity_states.items() if states and all(state == 'rejected' for state in states)}
    grouped = defaultdict(list)
    for node in nodes:
        if node.entity_id not in excluded_entities and node.status not in {'rejected', 'superseded'}:
            grouped[(node.entity_id, node.property)].append(node)
    issues, accepted = [], defaultdict(dict)
    for (entity, prop), candidates in grouped.items():
        selected = [node for node in candidates if node.status == 'accepted']
        distinct = {_json(node.value) for node in candidates if node.value is not None}
        if len(distinct) > 1:
            issues.append(_issue(candidates[0], 'conflicting_values', 'Conflicting source assertions remain distinct. Explicitly select or correct the accepted value.', candidates=candidates))
        elif len(selected) == 1 and not validate_value(prop, selected[0].value):
            accepted[entity][prop] = selected[0].value
        else:
            node = candidates[0]
            material = prop in REQUIRED or prop in {'project_start', 'project_finish', 'scope_complete'}
            code = 'missing_input' if node.value is None else 'review_required'
            if material or node.value is not None:
                issues.append(_issue(node, code, node.validation.get('error') or 'This value requires an explicit evidence review decision.',
                                     blocks=None if material else ['approval', 'export'],
                                     allowed=['correct'] if not node.sources or node.value is None else None))
    # Rejecting a required fact cannot silently turn an activity independent or
    # remove its duration requirement.
    for node in nodes:
        if node.entity_id not in excluded_entities and node.property in REQUIRED | {'project_start', 'project_finish', 'scope_complete'} and (node.entity_id, node.property) not in grouped:
            issues.append(_issue(node, 'missing_input', 'The required value was rejected; supply an approved replacement.', allowed=['correct']))
    activity_entities = {node.entity_id for node in nodes if node.property == 'duration'}
    accepted_activity_entities = {entity for entity in activity_entities if accepted.get(entity, {}).get('identity')}
    linked_entities = set()
    for edge in graph.edges.filter(current=True, relationship='same_as', source__current=True, target__current=True,
                                   source__status='accepted', target__status='accepted').select_related('source', 'target'):
        if not accepted.get(edge.source.entity_id, {}).get('identity') or not accepted.get(edge.target.entity_id, {}).get('identity'):
            continue
        if edge.source.entity_id in accepted_activity_entities:
            linked_entities.add(edge.target.entity_id)
        if edge.target.entity_id in accepted_activity_entities:
            linked_entities.add(edge.source.entity_id)
    for scope in graph.nodes.filter(current=True, kind='scope_link'):
        if scope.entity_id not in linked_entities | excluded_entities:
            identity = next((node for node in nodes if node.entity_id == scope.entity_id and node.property == 'identity'), None)
            if identity:
                issues.append(_issue(identity, 'scope_link_missing', 'Link this register item to its explicit schedule activity, or record its exclusion from scope.', allowed=['link', 'reject']))
    current_file_ids = [item['id'] for item in graph.source_manifest]
    for document in graph.documents.filter(source_file_id__in=current_file_ids, nodes__current=True).distinct():
        if document.integrity_status != 'verified':
            issues.append({'id': f'source_integrity:{document.id}', 'code': 'source_integrity_unverified', 'kind': 'source_integrity',
                           'title': document.filename, 'message': 'The original source file could not be read to verify its integrity. Restore access and refresh evidence.',
                           'entity_id': f'document:{document.id}', 'field': 'integrity', 'severity': 'error', 'status': 'open',
                           'candidate_fact_ids': [], 'allowed_actions': [], 'blocks': ['calculation', 'approval', 'export']})
    issues.extend({**item, 'id': f"{item['code']}:{item['entity_id']}", 'kind': item['code'], 'status': 'open',
                   'severity': 'error', 'title': item['message'], 'candidate_fact_ids': [],
                   'allowed_actions': [], 'blocks': ['calculation', 'approval', 'export']}
                  for item in network_issues({key: value for key, value in accepted.items() if key in activity_entities}))
    # Approved identity links keep both entities intact and expose cross-source
    # disagreements. A link never chooses a duration/date by precedence.
    by_entity = defaultdict(dict)
    for node in nodes:
        if node.status not in {'rejected', 'superseded'}:
            by_entity[node.entity_id].setdefault(node.property, []).append(node)
    for edge in graph.edges.filter(current=True, relationship='same_as').select_related('source', 'target'):
        left, right = by_entity[edge.source.entity_id], by_entity[edge.target.entity_id]
        for prop in set(left) & set(right) - {'identity'}:
            candidates = [node for node in left[prop] + right[prop] if node.value is not None]
            if len({_json(node.value) for node in candidates}) > 1:
                issues.append(_issue(candidates[0], 'linked_source_conflict', 'Approved identity link reveals different values. Neither source automatically takes precedence.', candidates=candidates))
    return nodes, dict(accepted), issues


def _graph(project):
    return EvidenceGraph.objects.filter(project=project).first()


def _readiness(graph, project, issues):
    stale = graph.source_fingerprint != input_fingerprint(project)
    reasons = [item['message'] for item in issues if 'calculation' in item['blocks']]
    approval_reasons = [item['message'] for item in issues if 'approval' in item['blocks']]
    if stale:
        reasons.insert(0, 'Sources or planning inputs changed. Refresh evidence and review affected facts.')
        approval_reasons.insert(0, reasons[0])
    no_scope = not graph.nodes.filter(current=True, kind='fact', property='identity', status='accepted').exists()
    if no_scope:
        reasons.append('No accepted planning scope is available.')
        approval_reasons.append('No accepted planning scope is available.')
    return {'stale': stale, 'calculation': {'ready': not reasons, 'reasons': reasons},
            'baseline': {'eligible': False, 'reasons': approval_reasons + ['Calculate the accepted plan and complete authorized schedule approval.']},
            'accepted_knowledge_ready': not approval_reasons}


def _category(node):
    if node.entity_id.startswith('assertion:'):
        return node.property
    if node.entity_id == 'project':
        return 'project'
    return 'activity' if node.property != 'identity' else 'deliverable'


def _serialize_fact(node):
    return {'id': str(node.id), 'entity_id': node.entity_id, 'entity_name': node.entity_name,
            'property': node.property, 'value': node.value, 'unit': node.unit or None, 'status': node.status,
            'provenance_type': node.provenance_type or None, 'sources': node.sources,
            'confidence': node.confidence, 'validation': node.validation, 'rule': node.rule,
            'input_schema': input_schema(node.property), 'category': _category(node)}


def evidence_review(project, *, offset=0, limit=100, fact_id=None):
    graph = _graph(project)
    empty = {'graph_id': None, 'revision': 0, 'facts': [], 'issues': [], 'decisions': [], 'category_summary': [],
             'readiness': {'stale': True, 'calculation': {'ready': False, 'reasons': ['Refresh evidence to build the review queue.']},
                           'baseline': {'eligible': False, 'reasons': ['Evidence has not been reviewed.']}}}
    if not graph:
        return empty
    nodes, accepted, issues = _knowledge(graph)
    issues.sort(key=lambda item: ('calculation' not in item['blocks'], item['id']))
    selected = issues[offset:offset + limit]
    focused = next((node for node in nodes if str(node.pk) == str(fact_id)), None) if fact_id else None
    if focused and not any(str(focused.pk) in item.get('candidate_fact_ids', []) for item in selected):
        selected = [_issue(focused, 'inspect_fact', 'Inspect the recorded value and its review history.', blocks=[])] + selected
    ids = {key for item in selected for key in item.get('candidate_fact_ids', [])}
    return {'graph_id': str(graph.id), 'revision': graph.revision, 'schema_version': graph.schema_version,
            'rule_version': graph.rule_version, 'readiness': _readiness(graph, project, issues),
            'issues': selected, 'facts': [_serialize_fact(node) for node in nodes if str(node.id) in ids],
            'pagination': {'offset': offset, 'limit': limit, 'total': len(issues), 'has_more': offset + limit < len(issues),
                           'next_offset': offset + limit if offset + limit < len(issues) else None},
            'category_summary': [{'category': category, 'label': category.replace('_', ' ').title(), 'count': count}
                                 for category, count in sorted(Counter(_category(node) for node in nodes).items())],
            'summary': {'fact_count': len(nodes), 'accepted_count': sum(node.status == 'accepted' for node in nodes),
                        'open_issue_count': len(issues)},
            'decisions': [{'id': str(item.id), 'action': item.action, 'fact_id': str(item.fact_id),
                           'target_fact_id': str(item.target_fact_id) if item.target_fact_id else None,
                           'actor_id': str(item.actor_id), 'reason': item.reason, 'created_at': item.created_at.isoformat()}
                          for item in graph.decisions.order_by('-created_at')[:50]],
            'messages': ['Review decisions update accepted knowledge. They do not overwrite the current schedule.']}


@transaction.atomic
def record_evidence_decision(project, actor, data):
    _require_write(project, actor)
    project = PlanningProject.objects.select_for_update().get(pk=project.pk)
    graph = EvidenceGraph.objects.select_for_update().filter(project=project).first()
    if not graph or data['revision'] != graph.revision:
        raise EvidenceError('Evidence changed. Refresh the review before recording your decision.', 'evidence_revision_conflict')
    if graph.source_fingerprint != input_fingerprint(project):
        raise EvidenceError('Source documents or planning inputs changed. Refresh evidence first.', 'evidence_sources_changed')
    node = graph.nodes.filter(pk=data.get('fact_id'), current=True, kind='fact').first()
    if not node:
        raise EvidenceError('Select a current fact from this project.', 'evidence_fact_not_found', 404)
    action, reason = data['action'], data['reason'].strip()
    if not reason:
        raise EvidenceError('A review reason is required.', status=400)
    target = None
    value = deepcopy(data.get('value')) if action == 'correct' else node.value
    if action == 'accept':
        if not node.sources or not node.validation.get('quote_verified') or node.provenance_type != 'document_evidence':
            raise EvidenceError('This value has no verified document fragment. Supply it as an approved planning input.', 'evidence_not_supported')
        error = validate_value(node.property, value)
        if error:
            raise EvidenceError(error, 'evidence_invalid_value', 400)
    elif action == 'correct':
        error = validate_value(node.property, value)
        if error:
            raise EvidenceError(error, 'evidence_invalid_value', 400)
    elif action == 'link':
        target = graph.nodes.filter(pk=data.get('target_fact_id'), current=True, property='identity').first()
        if node.property != 'identity' or not target or target.entity_id == node.entity_id:
            raise EvidenceError('Link two distinct current identity facts in the same project.', 'evidence_invalid_identity_link', 400)
    decision = EvidenceDecision.objects.create(graph=graph, graph_revision=graph.revision + 1, action=action,
        fact=node, target_fact=target, actor=actor, reason=reason, value=value,
        unit=data.get('unit') or '', source_fingerprint=graph.source_fingerprint)
    if action in {'accept', 'reject'}:
        node.status = 'accepted' if action == 'accept' else 'rejected'
        node.confidence = {**node.confidence, 'human_validation': node.status}
        node.save(update_fields=['status', 'confidence'])
        # Selecting a conflicting assertion is a recorded resolution. Retain
        # the other assertions as rejected, never overwrite their source values.
        if action == 'accept':
            graph.nodes.filter(current=True, kind='fact', entity_id=node.entity_id, property=node.property).exclude(pk=node.pk).update(status='rejected')
    elif action == 'correct':
        corrected = EvidenceNode.objects.create(id=_id(graph.id, decision.id), graph=graph, kind='fact',
            entity_id=node.entity_id, entity_name=node.entity_name, property=node.property, value=value,
            unit=data.get('unit') or '', provenance_type='approved_planning_input', status='accepted',
            sources=[], rule={'decision_id': str(decision.id), 'actor_id': str(actor.pk), 'reason': reason},
            confidence={'human_validation': 'accepted', 'extraction': {'kind': 'not_applicable'}, 'evidence_completeness': 'authorized_decision'},
            validation={'schema': SCHEMA_VERSION, 'error': None})
        graph.nodes.filter(current=True, kind='fact', entity_id=node.entity_id, property=node.property).exclude(pk=corrected.pk).update(status='superseded')
        EvidenceEdge.objects.create(id=_id(corrected.id, node.id, 'supersedes'), graph=graph, source=corrected, target=node,
            relationship='supersedes', provenance={'type': 'approved_planning_input', 'decision_id': str(decision.id)})
    elif action == 'link':
        EvidenceEdge.objects.get_or_create(id=_id(graph.id, *sorted([str(node.id), str(target.id)]), 'same_as'), defaults={
            'graph': graph, 'source': node, 'target': target, 'relationship': 'same_as',
            'provenance': {'type': 'approved_planning_input', 'decision_id': str(decision.id)}})
    graph.revision += 1
    graph.save(update_fields=['revision'])
    record_event(project=project, actor=actor, action=f'evidence.{action}', entity=decision,
                 after={'fact_id': str(node.id), 'target_fact_id': str(target.id) if target else None, 'revision': graph.revision})
    return decision


def approved_identity_links(project):
    graph = _graph(project)
    if not graph or graph.source_fingerprint != input_fingerprint(project):
        return []
    return [{'left_entity_id': edge.source.entity_id, 'right_entity_id': edge.target.entity_id,
             'decision_id': edge.provenance.get('decision_id')} for edge in graph.edges.filter(
                 current=True, relationship='same_as', source__current=True, target__current=True).select_related('source', 'target')]


def evidence_graph_snapshot(project):
    graph = _graph(project)
    if not graph:
        return {'schema_version': SCHEMA_VERSION, 'status': 'not_built', 'accepted_inputs': {}}
    nodes, accepted, issues = _knowledge(graph)
    return {'id': str(graph.id), 'revision': graph.revision, 'schema_version': graph.schema_version,
            'rule_version': graph.rule_version, 'source_fingerprint': graph.source_fingerprint,
            'source_manifest': graph.source_manifest, 'readiness': _readiness(graph, project, issues),
            'accepted_inputs': accepted, 'facts': [_serialize_fact(node) for node in nodes],
            'relationships': [{'id': str(edge.id), 'source': str(edge.source_id), 'target': str(edge.target_id),
                               'type': edge.relationship, 'provenance': edge.provenance}
                              for edge in graph.edges.filter(current=True)],
            'documents': [{'id': str(doc.id), 'file_id': doc.source_file_id, 'file_sha256': doc.file_sha256,
                           'text_sha256': doc.text_sha256, 'integrity_status': doc.integrity_status,
                           'extraction_method': doc.extraction_method, 'coverage': doc.coverage}
                          for doc in graph.documents.filter(nodes__current=True).distinct()],
            'issues': issues}


def accepted_document_plan(project):
    snapshot = evidence_graph_snapshot(project)
    inputs = snapshot['accepted_inputs']
    facts = snapshot.get('facts') or []
    by_entity = defaultdict(dict)
    activity_entities = {fact['entity_id'] for fact in facts if fact['property'] == 'duration'}
    for fact in facts:
        if fact['status'] == 'accepted':
            by_entity[fact['entity_id']][fact['property']] = fact['id']
    activities = []
    for entity, values in inputs.items():
        if entity == 'project' or entity not in activity_entities:
            continue
        if not values.get('identity'):
            continue
        activities.append({'id': entity, 'name': values['identity'], 'duration': values.get('duration'),
                           'calendar': values.get('calendar'), 'dependencies': values.get('dependencies'), 'constraints': values.get('constraints'),
                           'activity_type': values.get('activity_type'), 'start_date': values.get('start_date'),
                           'finish_date': values.get('finish_date'), 'property_provenance': by_entity[entity],
                           'derivation': {'rule': 'accepted_fact_projection', 'version': RULE_VERSION},
                           'validation_status': 'accepted' if REQUIRED <= values.keys() else 'partial'})
    return {'policy': 'document_driven', 'graph_id': snapshot.get('id'), 'graph_revision': snapshot.get('revision'),
            'activities': activities, 'project_inputs': inputs.get('project', {}),
            'ready_for_calculation': snapshot.get('readiness', {}).get('calculation', {}).get('ready', False),
            'issues': snapshot.get('issues', []), 'rules': RULE_VERSION}


def validate_schedule_inputs(version):
    """Compare every material persisted calculation input with accepted values."""
    project = version.schedule.project
    snapshot = evidence_graph_snapshot(project)
    issues = list(snapshot.get('issues') or [])
    def issue(code, message, entity='', field=''):
        issues.append({'code': code, 'message': message, 'entity_id': entity, 'field': field,
                       'severity': 'error', 'blocks': ['calculation', 'approval', 'export']})
    if snapshot.get('status') == 'not_built' or snapshot.get('readiness', {}).get('stale'):
        issue('evidence_graph_not_current', 'Build and review evidence for the current source versions before calculation.')
        return issues
    inputs = snapshot['accepted_inputs']
    if str(version.schedule.planned_start) != inputs.get('project', {}).get('project_start'):
        issue('project_start_not_accepted', 'The schedule start does not match an accepted planning input.', 'project', 'project_start')
    if (str(project.planned_end_date) if project.planned_end_date else None) != inputs.get('project', {}).get('project_finish'):
        issue('project_finish_not_accepted', 'The contractual finish used for float does not match an accepted planning input.', 'project', 'project_finish')
    activities = list(version.activities.filter(is_deleted=False).select_related('calendar'))
    keys = {activity.pk: (activity.metadata.get('evidence_entity_id') or
                         ('task:' + str(activity.metadata['simple_task_id']) if activity.metadata.get('simple_task_id') else str(activity.metadata.get('source_activity_id') or '')))
            for activity in activities}
    expected_entities = {fact['entity_id'] for fact in snapshot['facts'] if fact['property'] == 'duration'
                         and inputs.get(fact['entity_id'], {}).get('identity')}
    if Counter(keys.values()) != Counter(expected_entities):
        issue('accepted_scope_mismatch', 'The schedule activity set differs from the accepted scope, or an accepted activity is duplicated.', field='identity')
    links = defaultdict(list)
    for link in version.relationships.filter(is_deleted=False):
        links[link.successor_id].append({'predecessor_id': keys.get(link.predecessor_id), 'type': link.relationship_type,
                                         'lag': float(link.lag_days), 'lag_unit': 'working_days'})
    for activity in activities:
        entity, values = keys[activity.pk], inputs.get(keys[activity.pk], {})
        if not entity or not values:
            issue('activity_evidence_not_accepted', 'Activity has no accepted evidence identity.', str(activity.pk), 'identity')
            continue
        actual = {'identity': activity.name, 'duration': {'value': float(activity.duration_days), 'unit': 'working_days'},
                  'activity_type': activity.activity_type, 'dependencies': sorted(links[activity.pk], key=_json),
                  'constraints': [] if activity.constraint_type == 'none' else [{'type': activity.constraint_type, 'date': str(activity.constraint_date)}]}
        calendar = activity.calendar or version.schedule.default_calendar
        actual['calendar'] = None if not calendar else {'working_weekdays': calendar.working_weekdays,
            'hours_per_day': float(calendar.hours_per_day), 'timezone': calendar.timezone,
            **({'working_times': calendar.working_times} if calendar.working_times else {}),
            'exceptions': [{'date': str(item.date), 'is_working': item.is_working,
                            **({'working_times': item.working_times} if item.working_times else {}),
                            **({'working_hours': float(item.working_hours)} if item.working_hours is not None else {})}
                           for item in calendar.exceptions.filter(is_deleted=False).order_by('date')]}
        for prop, value in actual.items():
            expected = values.get(prop)
            if prop == 'duration' and isinstance(expected, dict):
                # The source quantity may also retain its verbatim `raw` cell.
                # Compare the declared numeric value/unit, preserving raw text
                # in the immutable fact referenced by this deterministic rule.
                expected = {key: expected.get(key) for key in ('value', 'unit')}
            if prop == 'dependencies' and isinstance(expected, list):
                expected = sorted(expected, key=_json)
            if prop == 'calendar' and isinstance(expected, dict):
                expected = {**expected, 'exceptions': sorted(expected.get('exceptions', []), key=lambda item: item['date'])}
                expected = {key: item for key, item in expected.items() if key != 'working_times' or item}
                expected['exceptions'] = [{key: value for key, value in item.items() if key != 'working_times' or value}
                                          for item in expected['exceptions']]
            if value != expected:
                issue('schedule_input_not_accepted', f'The current {prop} differs from the accepted evidence or decision.', entity, prop)
    return issues


@transaction.atomic
def materialize_accepted_plan(project, actor, *, revision):
    """Explicit command: accepted knowledge → a new, unapproved schedule draft."""
    from ..models import CalendarException, Schedule, ScheduleActivity, ScheduleVersion, ScheduleWBSNode, ActivityRelationship, WorkCalendar
    _require_write(project, actor)
    project = PlanningProject.objects.select_for_update().get(pk=project.pk)
    graph = _graph(project)
    if not graph or graph.revision != revision:
        raise EvidenceError('Evidence changed. Review the current accepted inputs first.', 'evidence_revision_conflict')
    snapshot = evidence_graph_snapshot(project)
    plan = accepted_document_plan(project)
    if not plan['ready_for_calculation']:
        raise EvidenceError('Resolve the blocking evidence issues before creating a schedule.', 'accepted_inputs_incomplete')
    existing = ScheduleVersion.objects.filter(evidence_graph=graph, evidence_graph_revision=revision, planning_build__isnull=True).first()
    if existing:
        if existing.is_deleted or existing.schedule.is_deleted:
            raise EvidenceError('This accepted projection was archived. Record a new review revision to create another.', 'accepted_projection_archived')
        if any('calculation' in item.get('blocks', []) for item in validate_schedule_inputs(existing)):
            raise EvidenceError('The existing projection differs from accepted inputs. Review the edits before creating another version.', 'accepted_projection_changed')
        return {'schedule_version_id': existing.pk, 'created': False, 'policy': 'document_driven', 'calculated': bool(existing.calculated_at)}
    rows = plan['activities']
    if not rows:
        raise EvidenceError('No accepted activities are available.', 'accepted_scope_empty')
    calendar_input = plan['project_inputs'].get('calendar')
    calendar_quantities = [calendar_input['hours_per_day']] + [
        item['working_hours'] for item in calendar_input['exceptions'] if item.get('working_hours') is not None]
    for quantity in calendar_quantities:
        try:
            number = Decimal(str(quantity))
            supported = number.is_finite() and 0 <= number <= 24 and number == number.quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            supported = False
        if not supported:
            raise EvidenceError('The schedule calendar adapter preserves at most two decimal places for hours between 0 and 24. The accepted quantity was not rounded or changed.',
                                'calendar_precision_unsupported')
    if (str(project.planned_end_date) if project.planned_end_date else None) != plan['project_inputs'].get('project_finish'):
        raise EvidenceError('Set the registered project finish to the approved finish before creating a schedule. Project dates are not changed automatically.', 'project_finish_not_accepted')
    if len(project.name) > 255:
        raise EvidenceError('The accepted project name exceeds this schedule adapter limit.', 'schedule_identity_limit')
    for row in rows:
        quantity = row.get('duration') or {}
        if len(row['name']) > 500 or Decimal(str(quantity.get('value'))) > Decimal('99999999.99'):
            raise EvidenceError('The accepted activity identity or duration exceeds this schedule adapter limit. The original value is preserved.', 'schedule_value_limit')
        if quantity.get('unit') != 'working_days' or Decimal(str(quantity.get('value'))) % 1:
            raise EvidenceError('The current calculation adapter supports whole working days only. The source quantity is preserved.', 'duration_resolution_unsupported')
        if row.get('calendar') != calendar_input:
            raise EvidenceError('The current calculation adapter supports one explicitly assigned calendar.', 'mixed_calendars_unsupported')
        if row['activity_type'] in {'start_milestone', 'finish_milestone'} and quantity['value'] != 0:
            raise EvidenceError('Resolve the milestone duration conflict.', 'milestone_duration_conflict')
        if len(row['constraints']) > 1:
            raise EvidenceError('The current calculation adapter supports one explicit date constraint per activity.', 'multiple_constraints_unsupported')
        if any(abs(Decimal(str(link['lag']))) > Decimal('999999.99') for link in row['dependencies']):
            raise EvidenceError('The accepted relationship lag exceeds this schedule adapter limit.', 'schedule_value_limit')
        if any(link['lag_unit'] != 'working_days' or Decimal(str(link['lag'])) % 1 for link in row['dependencies']):
            raise EvidenceError('The current calculation adapter supports whole working-day lags only.', 'lag_resolution_unsupported')
    calendar = WorkCalendar.objects.create(project=project, name=f'Accepted calendar {str(graph.pk)[:8]} r{revision}',
        working_weekdays=calendar_input['working_weekdays'], hours_per_day=calendar_input['hours_per_day'], timezone=calendar_input['timezone'],
        working_times=calendar_input.get('working_times', {}))
    CalendarException.objects.bulk_create([CalendarException(calendar=calendar, date=item['date'], is_working=item['is_working'],
        working_hours=item.get('working_hours'), working_times=item.get('working_times', []), name='Accepted evidence decision') for item in calendar_input['exceptions']])
    schedule, _ = Schedule.objects.get_or_create(project=project, code=f'EVIDENCE-{graph.pk.hex[:16]}', defaults={
        'name': project.name, 'planned_start': plan['project_inputs']['project_start'], 'default_calendar': calendar, 'created_by': actor})
    # Schedule-level calendars/start are shared by versions. New projections use
    # a new schedule if those inputs changed; prior approved versions stay intact.
    if str(schedule.planned_start) != plan['project_inputs']['project_start'] or schedule.versions.exists():
        schedule = Schedule.objects.create(project=project, code=f'EVIDENCE-{graph.pk.hex[:16]}-R{revision}',
            name=project.name, planned_start=plan['project_inputs']['project_start'], default_calendar=calendar, created_by=actor)
    version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='draft', created_by=actor,
        evidence_graph=graph, evidence_graph_revision=revision, evidence_input_snapshot=snapshot,
        change_summary=f'Accepted evidence graph revision {revision}; {RULE_VERSION}')
    wbs = ScheduleWBSNode.objects.create(version=version, code='1', name=project.name,
        level=0, sort_order=0)
    activities = {}
    for index, row in enumerate(rows):
        external_id = 'E-' + _hash({'graph': str(graph.pk), 'entity': row['id']})[:32]
        constraint = row['constraints'][0] if row['constraints'] else None
        activities[row['id']] = ScheduleActivity.objects.create(version=version, wbs_node=wbs, calendar=calendar,
            external_id=external_id, name=row['name'], activity_type=row['activity_type'], duration_days=row['duration']['value'],
            constraint_type=constraint['type'] if constraint else 'none', constraint_date=constraint['date'] if constraint else None,
            sort_order=index, metadata={'evidence_policy': 'document_driven', 'evidence_entity_id': row['id'],
                'property_provenance': row['property_provenance'], 'derivation': row['derivation']})
    for row in rows:
        for link in row['dependencies']:
            if link['predecessor_id'] not in activities:
                raise EvidenceError('The accepted predecessor is outside the selected scope.', 'dangling_predecessor')
            ActivityRelationship.objects.create(version=version, predecessor=activities[link['predecessor_id']],
                successor=activities[row['id']], relationship_type=link['type'], lag_days=link['lag'],
                metadata={'provenance_type': 'deterministic_derivation', 'rule_version': RULE_VERSION,
                          'fact_id': row['property_provenance']['dependencies'], 'lag_unit': link['lag_unit']})
    record_event(project=project, actor=actor, action='evidence_plan.materialized', entity=version,
        after={'graph_id': str(graph.pk), 'graph_revision': revision, 'activity_count': len(activities),
               'derivation': {'rule': 'accepted_fact_projection', 'version': RULE_VERSION}})
    return {'schedule_version_id': version.pk, 'created': True, 'policy': 'document_driven', 'calculated': False,
            'message': 'A new draft schedule was created from accepted inputs. Calculation and baseline approval remain separate actions.'}
