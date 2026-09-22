"""Explicit whole-graph review, independent of schedule creation or approval.

The actor authorizes the bulk operation. Source assertions remain immutable;
only supported values are selected, with an append-only decision for each
changed assertion. Model output can select existing verified candidates only.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import re

from django.db import transaction

from ..evidence_models import EvidenceDecision, EvidenceEdge, EvidenceGraph, EvidenceNode
from ..models import PlanningAuditEvent, PlanningJob, PlanningProject
from .audit import record_event
from .evidence_graph import EvidenceError, _current_documents, _hash, _id, _json, _knowledge, _require_write, input_fingerprint
from .evidence_schema import REQUIRED, RULE_VERSION, SCHEMA_VERSION, network_issues, validate_value
from .planning_fact_extraction import SUPPORTED_FACT_TYPES, quoted_value, validate_fact_value


ACTION = 'evidence.bulk_reviewed'
REVIEWED = {'accepted', 'rejected', 'superseded', 'corrected'}
BATCH_SIZE = 400


def issue_group_key(issue):
    return f"{issue.get('code') or issue.get('kind') or 'review_required'}:{issue.get('field') or ''}"


def _unresolved(issues):
    counts = Counter(issue_group_key(row) for row in issues)
    return [{'key': key, 'label': ' · '.join(part.replace('_', ' ').capitalize() for part in key.split(':') if part),
             'count': count} for key, count in sorted(counts.items())]


def _groups(nodes):
    result = defaultdict(list)
    for node in nodes:
        result[(node.entity_id, node.property)].append(node)
    return result


def _key(group):
    return _hash(list(group))


def _protected(graph, groups, issues):
    protected = {key for key, rows in groups.items() if any(row.status in REVIEWED for row in rows)}
    protected.update(graph.decisions.values_list('fact__entity_id', 'fact__property'))
    protected.update(graph.decisions.filter(target_fact__isnull=False).values_list('target_fact__entity_id', 'target_fact__property'))
    excluded = {key[0] for key, rows in groups.items() if key[1] == 'identity'
                and all(row.status in {'rejected', 'superseded'} for row in rows)}
    protected.update(key for key in groups if key[0] in excluded)
    conflicted_ids = {fact for issue in issues if issue.get('code') == 'linked_source_conflict'
                      for fact in issue.get('candidate_fact_ids', [])}
    protected.update(key for key, rows in groups.items() if any(str(row.pk) in conflicted_ids for row in rows))
    return protected


def _basic(node):
    return (node.current and node.kind == 'fact' and node.status not in REVIEWED
            and node.provenance_type == 'document_evidence' and isinstance(node.validation, dict)
            and node.validation.get('quote_verified') is True and not validate_value(node.property, node.value)
            and isinstance(node.sources, list) and bool(node.sources)
            and all(isinstance(source, dict) and source.get('quote_verified') is True
                    and all(source.get(key) for key in ('document_version', 'sha256', 'text_sha256', 'locator', 'verbatim'))
                    for source in node.sources))


def bulk_review_summary(project, *, nodes=None, issues=None):
    """Cheap eligibility indication; execution rechecks bytes and typed evidence."""
    from .evidence_bulk_ai import ai_availability
    ai = ai_availability(project)
    graph = EvidenceGraph.objects.filter(project=project).first()
    result = {'enabled': False, 'ai_available': ai['available'], 'ai_reason': ai.get('reason'),
              'provider': ai.get('provider'), 'model': ai.get('model'), 'total_open': 0,
              'verified_unambiguous': 0, 'conflict_groups': 0, 'unresolved_groups': [],
              'eligibility_rechecked_on_run': True,
              'count_basis': {'total_open': 'issues', 'verified_unambiguous': 'candidate groups',
                              'conflict_groups': 'candidate groups', 'unresolved_groups': 'issues'}}
    if graph is None:
        return result
    if nodes is None or issues is None:
        nodes, _, issues = _knowledge(graph)
    groups = _groups(nodes)
    protected = _protected(graph, groups, issues)
    verified_docs = {str(pk) for pk in graph.documents.filter(integrity_status='verified').values_list('pk', flat=True)}
    for key, candidates in groups.items():
        if key in protected or not all(_basic(node) and all(source['document_version'] in verified_docs
                                                          for source in node.sources) for node in candidates):
            continue
        field = 'verified_unambiguous' if len({_json(node.value) for node in candidates}) == 1 else 'conflict_groups'
        result[field] += 1
    result.update(enabled=bool(issues) and graph.schema_version == SCHEMA_VERSION and graph.rule_version == RULE_VERSION,
                  total_open=len(issues), unresolved_groups=_unresolved(issues))
    return result


def _locator_key(locator):
    for keys in (('character_start', 'character_end'), ('line',), ('page', 'row')):
        if all(type(locator.get(key)) is int for key in keys):
            return tuple((key, locator[key]) for key in keys)
    return None


class _SourceVerifier:
    """Read each original file once and index each document's rows/lines once."""
    def __init__(self, project, graph):
        self.documents, self.rows, self.lines, self.checked = {}, {}, {}, {}
        self.raw_bytes, self.repairs = {}, {}
        self.geometry = None
        files = {row.pk: row for row in project.files.filter(is_deleted=False).select_related('document_profile')}
        digests = {}
        self.files, self.digests = files, digests
        for document in _current_documents(graph):
            file = files.get(document.source_file_id)
            if (file is None or file.parse_status != 'done' or document.integrity_status != 'verified'
                    or document.storage_name != file.file.name
                    or document.text_sha256 != hashlib.sha256(document.extracted_text.encode('utf-8')).hexdigest()
                    or document.text_sha256 != hashlib.sha256((file.extracted_text or '').encode('utf-8')).hexdigest()):
                continue
            if file.pk not in digests:
                try:
                    digest = hashlib.sha256()
                    chunks, size = [], 0
                    retain = file.original_filename.lower().endswith('.pdf')
                    with file.file.open('rb') as stream:
                        for chunk in stream.chunks():
                            digest.update(chunk)
                            size += len(chunk)
                            if retain and size <= 32 * 1024 * 1024:
                                chunks.append(chunk)
                            else:
                                chunks = []
                                retain = False
                    digests[file.pk] = digest.hexdigest()
                    if retain:
                        self.raw_bytes[file.pk] = b''.join(chunks)
                except Exception:
                    # Storage backends use different unavailable/permission
                    # exceptions. An unreadable original never becomes evidence.
                    digests[file.pk] = None
            if digests[file.pk] == document.file_sha256:
                self.documents[str(document.pk)] = document

    def assert_unchanged(self, selections):
        referenced = {self.documents[source['document_version']].source_file_id
                      for selection in selections.values() for source in selection['node'].sources}
        for file_id in sorted(referenced):
            file = self.files[file_id]
            try:
                digest = hashlib.sha256()
                # Fresh storage handles prevent a previously buffered remote
                # file from hiding a same-path replacement during AI review.
                with file.file.storage.open(file.file.name, 'rb') as stream:
                    for chunk in stream.chunks():
                        digest.update(chunk)
                unchanged = digest.hexdigest() == self.digests[file_id]
            except Exception:
                unchanged = False
            if not unchanged:
                raise EvidenceError('An original source changed or became unavailable during review. No bulk decisions were applied.',
                                    'evidence_sources_changed')

    def source(self, source):
        key = _json(source)
        if key in self.checked:
            return self.checked[key]
        document = self.documents.get(source.get('document_version'))
        valid = bool(document and source.get('file_id') == document.source_file_id
                     and source.get('sha256') == document.file_sha256
                     and source.get('text_sha256') == document.text_sha256)
        quote, locator = source.get('verbatim'), source.get('locator') or {}
        if valid and isinstance(quote, str) and quote and isinstance(locator, dict):
            text = document.extracted_text
            if type(locator.get('character_start')) is int and type(locator.get('character_end')) is int:
                start, end = locator['character_start'], locator['character_end']
                valid = 0 <= start < end <= len(text) and text[start:end] == quote
            elif type(locator.get('line')) is int:
                doc_id = str(document.pk)
                if doc_id not in self.lines:
                    lines, offset = [], 0
                    for line in text.splitlines(keepends=True):
                        lines.append((offset, len(line)))
                        offset += len(line)
                    self.lines[doc_id] = lines
                lines, line = self.lines[doc_id], locator['line']
                valid = 0 < line <= len(lines)
                if valid:
                    offset, length = lines[line - 1]
                    position = text.find(quote, offset, offset + length + len(quote))
                    valid = offset <= position < offset + length
            elif type(locator.get('start')) is int and type(locator.get('end')) is int:
                start, end = locator['start'], locator['end']
                valid = 0 <= start < end <= len(text) and quote in text[start:end]
            else:
                valid = False
        else:
            valid = False
        self.checked[key] = bool(valid)
        return bool(valid)

    def _source_rows(self, document):
        doc_id = str(document.pk)
        if doc_id not in self.rows:
            from .structured_schedule_evidence import parse_structured_schedule_evidence
            from .reference_schedule_text import parse_reference_schedule_text
            rows = parse_structured_schedule_evidence(document.extracted_text)['rows']
            if not rows:
                rows = parse_reference_schedule_text(document.extracted_text)['rows']
            indexed = defaultdict(list)
            for row in rows:
                key = _locator_key(row.get('source_locator') or {})
                if key is not None:
                    indexed[key].append(row)
            self.rows[doc_id] = indexed
        return self.rows[doc_id]

    def grounded(self, node):
        if not _basic(node) or not all(self.source(source) for source in node.sources):
            return False
        # Extraction assertions require every supplied leaf to be in its exact
        # source quote; a nearby number or matching title is not enough.
        if node.property in SUPPORTED_FACT_TYPES:
            return any(validate_fact_value(node.property, node.value, source['verbatim']) for source in node.sources)
        if node.property in {'identity', 'phase', 'area'}:
            return any(quoted_value(node.value, source['verbatim']) for source in node.sources)
        # A machine-readable explicit property/value is a supported adapter.
        # Empty arrays, booleans and calendars cannot be proved by leaf matching.
        for source in node.sources:
            try:
                import json
                exact = json.loads(source['verbatim'])
            except (ValueError, TypeError):
                exact = None
            if isinstance(exact, dict) and node.property in exact and exact[node.property] == node.value:
                return True
            document = self.documents[source['document_version']]
            rows = self._source_rows(document).get(_locator_key(source['locator']), [])
            if len(rows) != 1:
                continue
            row = rows[0]
            values = row.get('values', row)
            mapping = {'start_date': 'planned_start_date', 'finish_date': 'planned_finish_date', 'duration': 'duration'}
            if node.property in mapping:
                expected = values.get(mapping[node.property])
                if node.property == 'duration' and isinstance(expected, dict) and isinstance(node.value, dict):
                    expected = {key: expected.get(key) for key in ('value', 'unit')}
                    actual = {key: node.value.get(key) for key in ('value', 'unit')}
                else:
                    actual = node.value
                if expected is not None and expected == actual:
                    return True
            if node.property == 'activity_type' and row.get('record_type'):
                if re.sub(r'\s+', '_', row['record_type'].strip().lower()) == node.value:
                    return True
            if node.property == 'dependencies' and values.get('predecessors') is not None:
                expected = [{'predecessor_id': str(link['id']), 'type': link.get('type'),
                             'lag': link.get('lag_days'), 'lag_unit': link.get('lag_unit')}
                            for link in values['predecessors']]
                if expected == node.value:
                    return True
        return False

    def candidate(self, node):
        if self.grounded(node):
            return node
        if (not node.current or node.kind != 'fact' or node.status in REVIEWED
                or node.provenance_type != 'document_evidence' or validate_value(node.property, node.value)
                or not isinstance(node.sources, list) or not node.sources):
            return None
        if not self.raw_bytes:
            return None
        from .evidence_bulk_sources import GeometryCitationVerifier
        if self.geometry is None:
            self.geometry = GeometryCitationVerifier()
        sources, proofs = [], []
        for source in node.sources:
            if not isinstance(source, dict):
                return None
            document = self.documents.get(source.get('document_version'))
            binary = self.raw_bytes.get(document.source_file_id) if document else None
            if not document or binary is None:
                return None
            repaired = self.geometry.repair(node, source, document, binary)
            if not repaired or repaired.get('value') != node.value or not self.source(repaired.get('source') or {}):
                return None
            proof = repaired.get('proof') or {}
            if (proof.get('original_fact_id') != str(node.pk) or proof.get('property') != node.property
                    or proof.get('value') != node.value or proof.get('document_version') != str(document.pk)):
                return None
            sources.append(repaired['source'])
            proofs.append(proof)
        candidate = deepcopy(node)
        candidate.sources = sources
        candidate.validation = {**node.validation, 'quote_verified': True, 'error': None, 'schema': SCHEMA_VERSION}
        self.repairs[str(node.pk)] = {'sources': sources, 'proofs': proofs}
        return candidate


def _snapshot(nodes):
    return _hash([{'id': str(node.pk), 'entity': node.entity_id, 'property': node.property,
                   'value': node.value, 'status': node.status, 'sources': node.sources,
                   'validation': node.validation, 'provenance': node.provenance_type}
                  for node in sorted(nodes, key=lambda row: str(row.pk))])


def _network_safe(nodes, selections):
    """Remove entire introduced dependency components when the DAG is invalid."""
    groups = _groups(nodes)
    entities = {node.entity_id for node in nodes if node.property == 'duration'}
    values = defaultdict(dict)
    for key, rows in groups.items():
        selected = selections.get(key)
        accepted = [row for row in rows if row.status == 'accepted']
        row = selected['node'] if selected else accepted[0] if len(accepted) == 1 else None
        if row and not validate_value(row.property, row.value):
            values[row.entity_id][row.property] = row.value
    network = {key: value for key, value in values.items() if key in entities and value.get('identity')}
    invalid = {row['entity_id'] for row in network_issues(network)}
    # Every new edge in an invalid connected component remains for review,
    # including edges into an existing accepted component. This conservative
    # bound avoids selecting an arbitrary edge to break a cycle.
    adjacency = defaultdict(set)
    for entity, value in network.items():
        for link in value.get('dependencies') or []:
            pred = link['predecessor_id']
            adjacency[entity].add(pred)
            adjacency[pred].add(entity)
    pending = list(invalid)
    while pending:
        for entity in adjacency[pending.pop()]:
            if entity not in invalid:
                invalid.add(entity)
                pending.append(entity)
    removed = []
    for key in list(selections):
        if key[1] == 'dependencies' and (key[0] not in network or key[0] in invalid):
            removed.append(key)
            del selections[key]
    return removed


def _replay(project, job, signature):
    if job is None:
        return None
    previous = PlanningAuditEvent.objects.filter(project=project, action=ACTION, metadata__job_id=str(job.pk)).first()
    if previous:
        if previous.metadata.get('request_signature') != signature:
            raise EvidenceError('The completed bulk review belongs to another request.', 'evidence_bulk_replay_conflict')
        return deepcopy(previous.after)
    return None


def run_bulk_evidence_review(project, actor, request_data, progress_callback=None, job=None):
    """Review all current issues, then apply one optimistic, audited transaction."""
    from .evidence_bulk_ai import ai_availability, resolve_conflicts
    if not isinstance(request_data, dict):
        raise EvidenceError('Supply a structured bulk review request.', 'evidence_bulk_request_invalid', 400)
    project = PlanningProject.objects.select_related('enterprise_project').get(pk=project.pk)
    _require_write(project, actor)
    mode, revision = request_data.get('mode', 'verified'), request_data.get('revision')
    reason = request_data.get('reason')
    if mode not in {'verified', 'ai_verified'} or type(revision) is not int or revision < 0:
        raise EvidenceError('Choose a valid review mode and current revision.', 'evidence_bulk_request_invalid', 400)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise EvidenceError('A review reason of at most 4,000 characters is required.', 'evidence_bulk_reason_required', 400)
    reason = reason.strip()
    signature = _hash({'project': project.pk, 'actor': str(actor.pk), 'mode': mode, 'revision': revision, 'reason': reason})
    if job is not None and (job.project_id != project.pk or job.requested_by_id != actor.pk or job.job_type != 'evidence_bulk'):
        raise EvidenceError('The bulk job does not belong to this project and actor.', 'evidence_bulk_job_forbidden', 403)
    replay = _replay(project, job, signature)
    if replay:
        return replay
    graph = EvidenceGraph.objects.filter(project=project).first()
    if graph is None or graph.revision != revision:
        raise EvidenceError('Evidence changed. Refresh before starting bulk review.', 'evidence_revision_conflict')
    if graph.schema_version != SCHEMA_VERSION or graph.rule_version != RULE_VERSION:
        raise EvidenceError('Refresh evidence using the current review rules.', 'evidence_rules_changed')
    fingerprint = input_fingerprint(project)
    if graph.source_fingerprint != fingerprint:
        raise EvidenceError('Source documents or planning inputs changed. Refresh evidence first.', 'evidence_sources_changed')
    if mode == 'ai_verified' and not ai_availability(project)['available']:
        raise EvidenceError('Configure the project AI provider before AI bulk review.', 'evidence_bulk_ai_unavailable', 400)

    def progress(percent, message, phase, **details):
        if progress_callback:
            progress_callback({'progress': percent, 'message': message, 'phase': phase, 'details': details})

    progress(10, 'Checking the complete evidence queue and original sources.', 'verify_sources')
    nodes, _, issues = _knowledge(graph)
    snapshot = _snapshot(nodes)
    groups, selected, ai_groups = _groups(nodes), {}, []
    protected = _protected(graph, groups, issues)
    verifier = _SourceVerifier(project, graph)
    skipped = len(protected & groups.keys())
    for key, candidates in groups.items():
        if key in protected:
            continue
        supported = [verifier.candidate(row) for row in candidates]
        if not all(supported):
            skipped += 1
            continue
        if len({_json(row.value) for row in candidates}) == 1:
            selected[key] = {'node': min(supported, key=lambda row: str(row.pk)), 'basis': 'verified'}
        elif mode == 'ai_verified':
            ai_groups.append({'key': _key(key), 'entity_id': key[0], 'property': key[1], 'candidates': [
                {'id': str(row.pk), 'entity_name': row.entity_name, 'value': row.value, 'unit': row.unit,
                 'sources': deepcopy(row.sources)} for row in supported]})
        else:
            skipped += 1
    warnings = []
    if ai_groups:
        progress(35, 'Reviewing verified conflicts with the configured project AI.', 'ai_review', groups=len(ai_groups))
        def ai_progress(update):
            total = max(1, update.get('total_groups', len(ai_groups)))
            done = update.get('completed_groups', 0)
            progress(35 + min(40, int(40 * done / total)), 'Reviewing verified conflicts with the project AI.',
                     'ai_review', **update)
        output = resolve_conflicts(ai_groups, project=project, actor=actor, progress_callback=ai_progress)
        warnings.extend(output.get('warnings') or [])
        by_key = {_key(key): key for key in groups}
        eligible = {group['key'] for group in ai_groups}
        decisions = output.get('decisions') or []
        counts = Counter(row.get('group_key') for row in decisions if isinstance(row, dict) and isinstance(row.get('group_key'), str))
        for decision in decisions:
            if (not isinstance(decision, dict) or not isinstance(decision.get('group_key'), str)
                    or decision.get('group_key') not in eligible or counts[decision['group_key']] != 1):
                continue
            key = by_key[decision['group_key']]
            members = {str(row.pk): verifier.candidate(row) for row in groups[key]}
            citations = decision.get('source_fact_ids')
            fact_id = decision.get('fact_id')
            if (not isinstance(fact_id, str) or fact_id not in members or decision.get('confidence') != 'high'
                    or not isinstance(decision.get('reason'), str) or not 12 <= len(decision['reason'].strip()) <= 6000
                    or not isinstance(citations, list) or not all(isinstance(value, str) for value in citations) or fact_id not in citations
                    or not set(citations) <= members.keys() or len(citations) != len(set(citations))):
                continue
            quotes = decision.get('evidence_quotes')
            supported = set()
            if not isinstance(quotes, list):
                continue
            for quote in quotes:
                if not isinstance(quote, dict) or not isinstance(quote.get('fact_id'), str):
                    break
                row = members.get(quote['fact_id'])
                index, text = quote.get('source_index'), quote.get('quote')
                if (row is None or quote['fact_id'] not in citations or type(index) is not int
                        or not 0 <= index < len(row.sources) or not isinstance(text, str) or not text.strip()
                        or text not in row.sources[index]['verbatim']):
                    break
                supported.add(quote['fact_id'])
            else:
                if set(citations) <= supported:
                    selected[key] = {'node': members[fact_id], 'basis': 'ai', 'ai': deepcopy(decision),
                                     'provider': output.get('provider'), 'model': output.get('model')}
        skipped += len(ai_groups) - sum(row['basis'] == 'ai' for row in selected.values())
    excluded = _network_safe(nodes, selected)
    skipped += len(excluded)
    if excluded:
        warnings.append('Proposed dependency groups that could introduce dangling links or cycles remain unresolved.')
    progress(85, 'Applying supported decisions after checking source and review revisions.', 'commit')
    with transaction.atomic():
        locked = PlanningProject.objects.select_for_update().select_related('enterprise_project').get(pk=project.pk)
        # A partial refresh leaves the reverse RBAC profile cached. Reload the
        # actor so revocation or a profile lock during external work is enforced.
        actor = type(actor)._default_manager.get(pk=actor.pk)
        _require_write(locked, actor)
        current = EvidenceGraph.objects.select_for_update().filter(project=locked).first()
        if job is not None:
            job = PlanningJob.objects.select_for_update().get(pk=job.pk)
            if job.project_id != locked.pk or job.requested_by_id != actor.pk or job.job_type != 'evidence_bulk':
                raise EvidenceError('The bulk job scope changed.', 'evidence_bulk_job_forbidden', 403)
            replay = _replay(locked, job, signature)
            if replay:
                return replay
            if job.status == 'cancelled':
                raise EvidenceError('This bulk review was cancelled.', 'evidence_bulk_cancelled')
        if (current is None or current.pk != graph.pk or current.revision != revision
                or current.source_fingerprint != fingerprint or input_fingerprint(locked) != fingerprint):
            raise EvidenceError('Evidence or sources changed during review. No bulk decisions were applied.', 'evidence_revision_conflict')
        current_nodes = list(current.nodes.filter(current=True, kind='fact'))
        if _snapshot(current_nodes) != snapshot:
            raise EvidenceError('Evidence changed during review. No bulk decisions were applied.', 'evidence_revision_conflict')
        verifier.assert_unchanged(selected)
        new_revision = revision + 1 if selected else revision
        decisions, changed, replacements, edges = [], [], [], []
        repaired_count = 0
        for key, selection in selected.items():
            winner = selection['node']
            original_winner = winner
            repair = verifier.repairs.get(str(winner.pk))
            if repair:
                replacement_id = _id(current.pk, 'verified_source_citation_repair', winner.pk, repair)
                winner = EvidenceNode(id=replacement_id, graph=current, document_version_id=original_winner.document_version_id,
                    kind='fact', entity_id=original_winner.entity_id, entity_name=original_winner.entity_name,
                    property=original_winner.property, value=deepcopy(original_winner.value), unit=original_winner.unit,
                    provenance_type='document_evidence', sources=deepcopy(repair['sources']),
                    rule={'name': 'verified_source_citation_repair', 'version': '1',
                          'original_fact_id': str(original_winner.pk), 'proofs': deepcopy(repair['proofs'])},
                    validation=deepcopy(original_winner.validation), confidence=deepcopy(original_winner.confidence),
                    status='accepted', current=True)
                replacements.append(winner)
                edges.append(EvidenceEdge(id=_id(replacement_id, original_winner.pk, 'supersedes'), graph=current,
                    source_id=replacement_id, target_id=original_winner.pk, relationship='supersedes',
                    provenance={'type': 'deterministic_derivation', 'rule': 'verified_source_citation_repair',
                                'version': '1', 'source_fingerprint': fingerprint, 'job_id': str(job.pk) if job else None}))
                original = next(row for row in groups[key] if row.pk == original_winner.pk)
                original.status = 'superseded'
                changed.append(original)
                repaired_count += 1
            rationale = {'bulk_mode': mode, 'selection_basis': selection['basis'], 'actor_id': str(actor.pk),
                         'job_id': str(job.pk) if job else None, 'selected_fact_id': str(winner.pk),
                         'group_key': _key(key), 'candidate_count': len(groups[key]),
                         'source_fact_ids': selection.get('ai', {}).get('source_fact_ids', [str(winner.pk)]),
                         **({'ai': selection['ai'], 'provider': selection.get('provider'), 'model': selection.get('model')}
                            if selection['basis'] == 'ai' else {})}
            for original in groups[key]:
                row = winner if original.pk == original_winner.pk else original
                action = 'accept' if original.pk == original_winner.pk else 'reject'
                decisions.append(EvidenceDecision(graph=current, graph_revision=new_revision, action=action,
                    fact_id=row.pk, actor=actor, reason=reason + '\nBulk review evidence: ' + _json(rationale),
                    value=deepcopy(row.value), unit=row.unit, source_fingerprint=fingerprint))
                row.status = 'accepted' if action == 'accept' else 'rejected'
                row.confidence = {**row.confidence, 'bulk_review': rationale,
                                  'human_validation': 'bulk_authorized', 'source_verification': 'original_bytes_and_typed_quote'}
                if not (repair and action == 'accept'):
                    changed.append(row)
        EvidenceNode.objects.bulk_create(replacements, batch_size=BATCH_SIZE)
        EvidenceEdge.objects.bulk_create(edges, batch_size=BATCH_SIZE)
        EvidenceDecision.objects.bulk_create(decisions, batch_size=BATCH_SIZE)
        EvidenceNode.objects.bulk_update(changed, ['status', 'confidence'], batch_size=BATCH_SIZE)
        if selected:
            current.revision = new_revision
            current.save(update_fields=['revision'])
        _, accepted, remaining = _knowledge(current)
        activity_entities = {row.entity_id for row in current_nodes if row.property == 'duration'}
        ready = (bool(activity_entities) and all(REQUIRED <= accepted.get(entity, {}).keys() for entity in activity_entities)
                 and {'project_start', 'project_finish', 'calendar', 'scope_complete'} <= accepted.get('project', {}).keys()
                 and not any('calculation' in issue.get('blocks', []) for issue in remaining))
        result = {'graph_id': str(current.pk), 'source_revision': revision, 'result_revision': new_revision, 'mode': mode,
                  'counts': {'processed': len(issues), 'total': len(issues),
                             'accepted_verified': sum(row['basis'] == 'verified' for row in selected.values()),
                             'accepted_ai': sum(row['basis'] == 'ai' for row in selected.values()),
                             'citations_repaired': repaired_count,
                             'rejected_alternatives': len(decisions) - len(selected),
                             'unresolved': len(remaining), 'skipped': skipped},
                  'count_basis': {'processed': 'issues', 'total': 'issues', 'accepted_verified': 'facts',
                                  'accepted_ai': 'facts', 'rejected_alternatives': 'facts',
                                  'citations_repaired': 'facts',
                                  'unresolved': 'issues', 'skipped': 'candidate groups'},
                  'unresolved_groups': _unresolved(remaining), 'review_complete': not remaining,
                  'calculation_ready': ready, 'warnings': warnings,
                  'schedule_changed': False, 'baseline_approved': False}
        record_event(project=locked, actor=actor, action=ACTION, entity=current,
                     before={'revision': revision, 'open_issue_count': len(issues)}, after=result,
                     metadata={'job_id': str(job.pk) if job else None, 'request_signature': signature,
                               'source_fingerprint': fingerprint, 'decision_count': len(decisions), 'reason': reason})
        if job is not None:
            job.result_data = deepcopy(result)
            job.save(update_fields=['result_data', 'updated_at'])
    return result
