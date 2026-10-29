"""Requester-scoped SQL outbox for incremental, source-cited Opportunity Analysis."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import PurePosixPath
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F, Q, Prefetch
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.rbac.action_policy import module_action_allowed
from .analysis_content import CHUNK_CHARS, document_references, extract_analysis_chunks
from .analysis_models import OpportunityAnalysis as Report, OpportunityAnalysisSource as Source, OpportunityAnalysisChunk as Chunk
from .email_ai_provider import analyze_opportunity_sources, email_ai_cache_identity, email_ai_configuration
from .email_permissions import visible_email_intakes
from .models import Deal, OpportunityWorkspaceUpload
from .opportunity_workspace import _actor, require_access, download_workspace_file

VERSION = 'opportunity_sales_review_v1'
REVIEW_VERSION = 'opportunity_business_risks_v2'


class ProviderUnavailable(Exception):
    pass
KINDS = ('brief', 'client_need', 'submission', 'risk', 'action', 'deadline', 'next_step',
         'capability_fit', 'strategic_fit', 'resources', 'commercial', 'schedule', 'clarification')
REVIEW_DIMENSIONS = ('capability_fit', 'strategic_fit', 'resources', 'execution_risk', 'commercial', 'schedule')
REVIEW_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['recommendation', 'rationale', 'rationale_evidence_ids', 'dimensions', 'business_risks', 'clarifications', 'suggested_letter'],
    'properties': {
        'recommendation': {'type': 'string', 'enum': ['go', 'conditional_go', 'no_go', 'insufficient_evidence']},
        'rationale': {'type': 'string'},
        'rationale_evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
        'dimensions': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['dimension', 'assessment', 'summary', 'evidence_ids'],
            'properties': {
                'dimension': {'type': 'string', 'enum': list(REVIEW_DIMENSIONS)},
                'assessment': {'type': 'string', 'enum': ['fit', 'gap', 'mixed', 'unknown']},
                'summary': {'type': 'string'},
                'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
            }}},
        'business_risks': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['category', 'risk', 'decision_impact', 'evidence_ids'],
            'properties': {
                'category': {'type': 'string', 'enum': ['attractiveness', 'profitability', 'winnability', 'commercial_exposure']},
                'risk': {'type': 'string'},
                'decision_impact': {'type': 'string'},
                'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
            }}},
        'clarifications': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['text', 'evidence_ids'],
            'properties': {'text': {'type': 'string'}, 'evidence_ids': {'type': 'array',
                'items': {'type': 'string'}}}}},
        'suggested_letter': {'type': 'string', 'enum': ['eoi', 'regret_expertise', 'regret_manpower', 'user_choice', 'none']},
    }}
PROVIDER_ERRORS = frozenset({
    'disabled', 'configuration_missing', 'configuration_invalid', 'unsupported_provider',
    'invalid_input', 'invalid_schema', 'invalid_instructions', 'input_too_large',
    'output_too_large', 'provider_timeout', 'provider_authentication', 'provider_permission',
    'provider_rate_limit', 'provider_request', 'provider_dependency_missing',
    'provider_unavailable', 'provider_refused', 'provider_incomplete', 'invalid_response',
})
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['claims'], 'properties': {
    'claims': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['kind', 'text', 'excerpt', 'date'], 'properties': {
            'kind': {'type': 'string', 'enum': list(KINDS)}, 'text': {'type': 'string'},
            'excerpt': {'type': 'string'}, 'date': {'type': 'string'},
        }}}}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def authority(deal, actor):
    if not actor or not actor.is_active:
        raise PermissionDenied('Opportunity analysis requires an active identity.')
    require_access(actor, deal, 'export')


def inventory(deal, actor):
    """No listing/page limit: every current tracked revision participates."""
    sources = []
    # Outgoing letters are stored in the same Correspondence folder as client
    # evidence. Exclude their exact attached upload IDs so an earlier draft
    # cannot become evidence for a later Go/No-Go recommendation.
    letter_files = set()
    for custom_data in deal.letters.values_list('custom_data', flat=True):
        attachments = custom_data.get('attachments') if isinstance(custom_data, dict) else None
        if not isinstance(attachments, dict):
            continue
        for entry in attachments.get('files') or []:
            if isinstance(entry, dict) and entry.get('status') == 'ready':
                file_data = entry.get('file')
                file_id = file_data.get('id') if isinstance(file_data, dict) else None
                if file_id:
                    letter_files.add((entry.get('provider'), str(file_id)))
    rows = OpportunityWorkspaceUpload.objects.filter(workspace__opportunity=deal, status='ready').filter(
        Q(document__isnull=True) | Q(document__head_upload_id=F('pk'))).order_by('created_at', 'pk')
    for row in rows:
        remote_id = str((row.result or {}).get('id') or '')
        file_id = 'radai-' + str(row.pk) if row.provider == 'radai' else remote_id
        if row.folder_key == 'correspondence' and (row.provider, file_id) in letter_files:
            continue
        metadata = {'name': row.name, 'folder': row.folder_key, 'size': row.size,
                    'provider': row.provider, 'upload_id': str(row.pk),
                    'file_id': file_id,
                    'version': row.version_number, 'sha256': row.sha256}
        sources.append({'key': 'upload:' + str(row.pk), 'identity': digest({
            'metadata': metadata, 'sha': row.sha256, 'storage': row.storage_fingerprint,
            'encoding': row.storage_encoding, 'stored_sha': row.stored_sha256,
            'remote': row.result}), 'metadata': metadata})
    if module_action_allowed(actor, 'sales_email_intake', 'read'):
        for intake in visible_email_intakes(actor).filter(opportunity=deal).order_by('received_at', 'pk'):
            sources.append({'key': 'intake:' + str(intake.pk), 'identity': digest({
                'subject': intake.subject, 'body': intake.body_preview, 'from': intake.sender_email,
                'date': intake.sent_at or intake.received_at}), 'metadata': {
                    'name': intake.subject, 'provider': 'email_intake', 'intake_id': str(intake.pk),
                    'size': len(intake.body_preview), 'folder': 'correspondence'}})
    return sources


def unique_sources(sources, suppressed):
    by_key = {item['key']: item for item in sources}
    valid = {
        key: entry for key, entry in suppressed.items()
        if key in by_key and isinstance(entry, dict)
        and by_key[key]['identity'] == entry.get('identity')
        and entry.get('representative') in by_key
        and by_key[entry['representative']]['identity'] == entry.get('representative_identity')
    }
    return [item for item in sources if item['key'] not in valid], valid


def _document_words(parts):
    return re.findall(r'\w+', ' '.join(text for _, text in parts).casefold())


def _same_document(parts, other_parts):
    words, other = _document_words(parts), _document_words(other_parts)
    # A one-word change can reverse a deadline or obligation in a tender.
    return bool(words) and words == other


def _duplicate_source(source, parts, diagnostic=''):
    suffix = PurePosixPath(source.metadata['name'].casefold()).suffix
    if (not source.source_key.startswith('upload:') or suffix not in {'.pdf', '.docx'}
            or not parts or diagnostic not in {'', 'document_body_only'}):
        return None
    stem = PurePosixPath(source.metadata['name'].casefold()).stem
    other_name = stem + ('.docx' if suffix == '.pdf' else '.pdf')
    for candidate in source.report.sources.filter(
        pk__lt=source.pk, source_key__startswith='upload:',
        status__in=['extracted', 'ready', 'partial'],
        metadata__name__iexact=other_name,
    ).order_by('pk'):
        name = PurePosixPath(candidate.metadata['name'].casefold())
        if name.stem != stem or name.suffix == suffix or candidate.diagnostic not in {'', 'document_body_only'}:
            continue
        other_parts = [(chunk.location, chunk.text) for chunk in candidate.chunks.order_by('ordinal')]
        if _same_document(parts, other_parts):
            return candidate
    return None


def _collapse_existing_duplicates(report):
    for source in report.sources.filter(status__in=['extracted', 'ready', 'partial']).order_by('pk'):
        if PurePosixPath(source.metadata['name'].casefold()).suffix not in {'.pdf', '.docx'}:
            continue
        parts = [(chunk.location, chunk.text) for chunk in source.chunks.order_by('ordinal')]
        duplicate = _duplicate_source(source, parts, source.diagnostic)
        if duplicate:
            report.suppressed_sources[source.source_key] = {
                'identity': source.identity, 'representative': duplicate.source_key,
                'representative_identity': duplicate.identity,
            }
            source.delete()


def snapshot(deal, actor, sources):
    from .private_attachments import _storage
    from .workspace_graph import workspace_config
    try:
        private_identity = _storage()[1]
    except Exception:
        private_identity = 'unavailable'
    remote = workspace_config()
    return digest({'version': VERSION, 'review_version': REVIEW_VERSION,
                   'provider': email_ai_cache_identity(), 'sources': sources,
                   'storage': private_identity, 'remote': remote.fingerprint if remote else '',
                   'opportunity': {'id': deal.pk, 'name': deal.deal_name, 'client': deal.client_id,
                                   'stage': deal.stage, 'deadline': deal.submission_due_date}})


def mark_dirty(opportunity_id):
    # Durable intent: broker failure cannot lose the invalidation.
    Report.objects.filter(opportunity_id=opportunity_id).update(
        dirty=True, status='queued', result={}, error_code='', next_attempt_at=timezone.now(),
        updated_at=timezone.now())


def request_analysis(deal, actor, retry=False):
    authority(deal, actor)
    with transaction.atomic():
        # Serialize first creation and concurrent refreshes per opportunity.
        Deal.objects.select_for_update().get(pk=deal.pk)
        report, _ = Report.objects.get_or_create(opportunity=deal, requested_by=actor)
        sources = inventory(deal, actor)
        current = snapshot(deal, actor, sources)
        if report.dirty or report.snapshot != current:
            report.dirty, report.status, report.result = True, 'queued', {}
            report.next_attempt_at, report.error_code = timezone.now(), ''
            report.save()
        elif retry and report.status in {'failed', 'partial', 'blocked'}:
            Chunk.objects.filter(source__report=report).filter(
                Q(status='failed') | Q(status='ready', error_code='invalid_evidence')
            ).update(status='queued', attempts=0, error_code='')
            Source.objects.filter(report=report, status='failed').update(status='queued', diagnostic='')
            report.status = 'queued'
            report.next_attempt_at, report.error_code = timezone.now(), ''
            report.save()
    return analysis_projection(deal, actor)


def analysis_projection(deal, actor, offset=None):
    authority(deal, actor)
    report = Report.objects.filter(opportunity=deal, requested_by=actor).first()
    configuration = email_ai_configuration()
    base = {'opportunity_id': str(deal.pk), 'ai_ready': configuration['ready'],
            'provider': configuration['provider'], 'model': configuration['model'],
            'coverage_note': 'Includes current RADAI files, tracked SharePoint uploads and accessible linked emails. '
                             'Untracked SharePoint changes and embedded email attachments are not automatically imported.'}
    if not report:
        return {**base, 'status': 'not_started', 'result': {}, 'counts': {}, 'sources': [], 'next_offset': None}
    current_sources = inventory(deal, actor)
    stale = report.dirty or report.snapshot != snapshot(deal, actor, current_sources)
    rows = report.sources.order_by('id')
    counts = dict(Counter(rows.values_list('status', flat=True)))
    counts['total'] = len(unique_sources(current_sources, report.suppressed_sources)[0])
    counts['chunks_total'] = Chunk.objects.filter(source__report=report).count()
    counts['chunks_done'] = Chunk.objects.filter(source__report=report, status='ready').count()
    counts['chunks_failed'] = Chunk.objects.filter(source__report=report, status='failed').count()
    if stale:
        counts = {'total': len(unique_sources(current_sources, report.suppressed_sources)[0]),
                  'chunks_total': 0, 'chunks_done': 0}
    page = []
    if offset is not None and not stale:
        for row in rows[offset:offset + 25].prefetch_related(Prefetch('chunks', queryset=Chunk.objects.defer('text'))):
            claims = [dict(item, location=chunk.location) for chunk in row.chunks.all()
                      if chunk.status == 'ready' for item in chunk.claims]
            failed = any(chunk.status == 'failed' for chunk in row.chunks.all())
            page.append({'id': row.pk, **row.metadata, 'status': row.status, 'diagnostic': row.diagnostic,
                         'analysis_failed': failed, 'analysis_error': next(
                             (chunk.error_code for chunk in row.chunks.all() if chunk.error_code), ''),
                         'references': row.references,
                         'claims': claims[:100], 'claims_total': len(claims)})
    return {**base, 'id': str(report.pk), 'status': 'queued' if stale else report.status,
            'stale': stale, 'updated_at': report.updated_at.isoformat(), 'counts': counts,
            'result': {} if stale else report.result, 'error_code': report.error_code,
            'sources': page, 'next_offset': offset + 25 if offset is not None and not stale and rows.count() > offset + 25 else None}


def due_analysis_ids():
    now = timezone.now()
    return list(Report.objects.filter(status__in=['queued', 'running']).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lt=now)).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)).order_by('updated_at').values_list('pk', flat=True)[:50])


def _date_supported(value, excerpt):
    if not value:
        return True
    try:
        date = datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return False
    normalized = excerpt.lower().replace(',', '').replace('/', '-').replace('.', '-')
    normalized = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', normalized)
    normalized = ' '.join(normalized.split())
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d %B %Y', '%d %b %Y', '%B %d %Y', '%b %d %Y'):
        candidate = date.strftime(fmt).lower()
        if candidate in normalized or candidate.replace(' 0', ' ').lstrip('0') in normalized:
            return True
    return False


def _source_excerpt(excerpt, text):
    if excerpt in text:
        return excerpt
    # PDF/Office text often contains line breaks where the model quotes spaces.
    match = re.search(r'\s+'.join(re.escape(part) for part in excerpt.split()), text)
    return match.group(0) if match and len(match.group(0)) <= 1200 else None


def _verified_claims(proposal, text):
    if not isinstance(proposal, dict) or set(proposal) != {'claims'} or not isinstance(proposal['claims'], list) or len(proposal['claims']) > 16:
        raise ValueError('invalid_output')
    result, rejected = [], 0
    for item in proposal['claims']:
        if (not isinstance(item, dict) or set(item) != {'kind', 'text', 'excerpt', 'date'}
                or item['kind'] not in KINDS
                or not all(isinstance(item[key], str) for key in ('text', 'excerpt', 'date'))
                or not 1 <= len(item['text']) <= 1000 or not 8 <= len(item['excerpt']) <= 1200):
            rejected += 1
            continue
        excerpt = _source_excerpt(item['excerpt'], text)
        if not excerpt or not _date_supported(item['date'], excerpt):
            rejected += 1
            continue
        result.append({**item, 'excerpt': excerpt})
    return result, rejected


def validate_claims(proposal, text):
    result, rejected = _verified_claims(proposal, text)
    if rejected:
        raise ValueError('invalid_evidence')
    return result


def _extract_source(source, deal, actor):
    metadata = source.metadata
    if metadata['provider'] == 'email_intake':
        intake = visible_email_intakes(actor).get(pk=metadata['intake_id'], opportunity=deal)
        if not module_action_allowed(actor, 'sales_email_intake', 'read'):
            raise PermissionDenied()
        text = f'From: {intake.sender_name} <{intake.sender_email}>\nSubject: {intake.subject}\n\n{intake.body_preview}'
        chunks = [(f'Email part {index // CHUNK_CHARS + 1}', text[index:index + CHUNK_CHARS])
                  for index in range(0, len(text), CHUNK_CHARS)]
        return chunks, 'email_preview_only'
    if not metadata.get('file_id'):
        return [], 'remote_identity_unavailable'
    stream, name = download_workspace_file(deal, actor, metadata['folder'], metadata['file_id'])
    try:
        return extract_analysis_chunks(stream, name, metadata['size'])
    finally:
        stream.close()


def _compile(report):
    """Cited executive digest over all extracted facts; no invented synthesis."""
    sections = {kind: [] for kind in KINDS}
    item_counts = {kind: 0 for kind in KINDS}
    refs, identical = defaultdict(list), defaultdict(list)
    deadline_dates = set()
    today = timezone.now().astimezone(ZoneInfo('Asia/Dubai')).date().isoformat()
    deadline_order = lambda item: (0 if item['date'] and item['date'] >= today else 1 if item['date'] else 2, item['date'])
    sources = report.sources.order_by('id')
    seen = set()
    for source in sources:
        label = {'source_id': source.pk, 'name': source.metadata['name']}
        if source.metadata.get('sha256'):
            identical[source.metadata['sha256']].append(label)
        for ref in source.references:
            refs[ref.upper()].append(label)
        for chunk in source.chunks.filter(status='ready').defer('text').iterator(chunk_size=100):
            for claim in chunk.claims:
                key = digest((claim['kind'], claim['text'], claim['date']))
                if key in seen:
                    continue
                seen.add(key)
                kind = claim['kind']
                if kind == 'deadline' and claim['date']:
                    deadline_dates.add(claim['date'])
                item_counts[kind] += 1
                item = {**claim, **label, 'location': chunk.location,
                        'file_id': source.metadata.get('file_id'), 'folder': source.metadata.get('folder')}
                if len(sections[kind]) < 12:
                    sections[kind].append(item)
                elif kind == 'deadline':
                    sections[kind].append(item)
                    sections[kind].sort(key=deadline_order)
                    sections[kind] = sections[kind][:12]
    sections['deadline'].sort(key=deadline_order)
    relationships = [{'kind': 'shared_reference', 'reference': key, 'sources': values}
                     for key, values in refs.items() if len(values) > 1]
    relationships.extend({'kind': 'identical_content', 'reference': 'Same file content', 'sources': values}
                         for values in identical.values() if len(values) > 1)
    dates = sorted(deadline_dates)
    # Multiple dates are a review signal, never an assertion that all refer to
    # the same milestone or that the latest document overrides earlier evidence.
    return {'sections': {kind: values[:12] for kind, values in sections.items()},
            'item_counts': item_counts,
            'relationships': relationships[:50], 'deadline_dates': dates,
            'review_note': 'AI findings and recommendations require review. Source excerpts support traceability, '
                           'not legal interpretation or approval. Multiple dates may refer to different milestones.'}


def _manager_review(report, deal, compiled):
    """Synthesize a cited, advisory Sales review from verified extracted claims."""
    from .email_ai_provider import analyze_opportunity_review_sources

    claims = []
    evidence = {}
    record_values = {
        'Opportunity reference': deal.deal_code,
        'Opportunity title': deal.deal_name,
        'Client': getattr(deal.client, 'company_name', ''),
        'Opportunity type': deal.opportunity_type,
        'Scope type': deal.scope_type,
        'Service categories': ', '.join(deal.service_categories or []),
        'Estimated value': f'{deal.estimated_value} {deal.currency}'.strip() if deal.estimated_value is not None else '',
        'Submission deadline': str(deal.submission_due_date or ''),
        'Expected award date': str(deal.expected_close_date or ''),
    }
    record_value_count = sum(bool(value) for value in record_values.values())
    for field, value in record_values.items():
        if value:
            evidence_id = f'record:{field.casefold().replace(" ", "_")}'
            evidence[evidence_id] = {'source_id': 'opportunity', 'name': 'Saved Opportunity record',
                                     'location': field, 'text': str(value), 'excerpt': str(value)}
            claims.append({'id': evidence_id, 'kind': 'opportunity_record', 'text': str(value)[:300],
                           'excerpt': str(value)[:300], 'source': 'Saved Opportunity record', 'location': field})
    claim_limits = {'brief': 3, 'client_need': 3, 'submission': 4, 'risk': 12,
                    'action': 2, 'deadline': 3, 'next_step': 2, 'capability_fit': 4,
                    'strategic_fit': 4, 'resources': 5, 'execution_risk': 4,
                    'commercial': 12, 'schedule': 5, 'clarification': 4}
    source_claim_count = sum(min(limit, len(compiled['sections'].get(kind, [])))
                             for kind, limit in claim_limits.items())
    for kind, limit in claim_limits.items():
        # Keep the synthesis input bounded. The complete source analysis remains
        # available in the cited sections and Sources view.
        for index, item in enumerate(compiled['sections'].get(kind, [])[:limit]):
            evidence_id = f"{item['source_id']}:{kind}:{index}"
            evidence[evidence_id] = item
            claims.append({'id': evidence_id, 'kind': kind, 'text': item['text'][:300],
                           'excerpt': item['excerpt'][:300], 'source': item['name'][:120],
                           'location': item['location'][:120]})
    facts = {
        'reference': deal.deal_code, 'title': deal.deal_name, 'opportunity_type': deal.opportunity_type,
        'client': getattr(deal.client, 'company_name', ''), 'scope_type': deal.scope_type,
        'service_categories': deal.service_categories or [],
        'estimated_value': str(deal.estimated_value) if deal.estimated_value is not None else '',
        'currency': deal.currency, 'submission_due_date': str(deal.submission_due_date or ''),
        'expected_close_date': str(deal.expected_close_date or ''),
        'internal_company_information': 'Not provided. Capability inventory, strategic priorities, current resource availability, margin and commercial thresholds are unknown.',
    }
    if not source_claim_count:
        return {'status': 'ready', 'recommendation': 'insufficient_evidence',
                'rationale': 'No verified tender evidence is available for a Go/No-Go recommendation.',
                'rationale_evidence': [],
                'dimensions': [{'dimension': key, 'assessment': 'unknown',
                                'summary': 'No verified evidence available.', 'evidence': []}
                               for key in REVIEW_DIMENSIONS],
                'business_risks': [], 'clarifications': [], 'suggested_letter': 'none', 'coverage': {'source_claims_considered': 0,
                    'opportunity_fields_considered': record_value_count, 'sources': report.sources.count()},
                'review_note': 'No verified tender evidence is available. This is not a No-Go decision.'}
    response = analyze_opportunity_review_sources({'opportunity': facts, 'evidence': claims}, REVIEW_SCHEMA)
    if response['status'] != 'completed':
        return {'status': 'unavailable', 'error_code': response.get('error_code') or 'provider_unavailable'}
    proposal = response.get('proposal')
    if not isinstance(proposal, dict):
        return {'status': 'unavailable', 'error_code': 'invalid_output'}
    try:
        if set(proposal) != {'recommendation', 'rationale', 'rationale_evidence_ids', 'dimensions', 'business_risks', 'clarifications', 'suggested_letter'}:
            raise ValueError()
        if proposal['recommendation'] not in {'go', 'conditional_go', 'no_go', 'insufficient_evidence'}:
            raise ValueError()
        if proposal['suggested_letter'] not in {'eoi', 'regret_expertise', 'regret_manpower', 'user_choice', 'none'}:
            raise ValueError()
        if not isinstance(proposal['rationale'], str):
            raise ValueError()
        proposal['rationale'] = proposal['rationale'][:1800]
        if (not isinstance(proposal['rationale_evidence_ids'], list)
                or any(key not in evidence for key in proposal['rationale_evidence_ids'])):
            raise ValueError()
        proposal['rationale_evidence_ids'] = proposal['rationale_evidence_ids'][:6]
        if proposal['recommendation'] != 'insufficient_evidence' and not proposal['rationale_evidence_ids']:
            raise ValueError()
        dimensions = proposal['dimensions']
        if not isinstance(dimensions, list) or len(dimensions) != len(REVIEW_DIMENSIONS):
            raise ValueError()
        seen = set()
        for item in dimensions:
            if (not isinstance(item, dict) or set(item) != {'dimension', 'assessment', 'summary', 'evidence_ids'}
                    or item['dimension'] not in REVIEW_DIMENSIONS or item['dimension'] in seen
                    or item['assessment'] not in {'fit', 'gap', 'mixed', 'unknown'}
                    or not isinstance(item['summary'], str)
                    or not isinstance(item['evidence_ids'], list)
                    or any(key not in evidence for key in item['evidence_ids'])):
                raise ValueError()
            seen.add(item['dimension'])
            item['summary'] = item['summary'][:700]
            # Unknown internal facts cannot be established by tender citations.
            item['evidence_ids'] = [] if item['assessment'] == 'unknown' else item['evidence_ids'][:4]
        if seen != set(REVIEW_DIMENSIONS):
            raise ValueError()
        if not isinstance(proposal['business_risks'], list):
            raise ValueError()
        proposal['business_risks'] = proposal['business_risks'][:5]
        for item in proposal['business_risks']:
            if (not isinstance(item, dict)
                    or set(item) != {'category', 'risk', 'decision_impact', 'evidence_ids'}
                    or item['category'] not in {'attractiveness', 'profitability', 'winnability', 'commercial_exposure'}
                    or not isinstance(item['risk'], str) or not isinstance(item['decision_impact'], str)
                    or not isinstance(item['evidence_ids'], list)
                    or any(key not in evidence for key in item['evidence_ids'])):
                raise ValueError()
            item['risk'] = item['risk'][:500]
            item['decision_impact'] = item['decision_impact'][:500]
            item['evidence_ids'] = item['evidence_ids'][:4]
        proposal['business_risks'] = [item for item in proposal['business_risks'] if item['evidence_ids']
                                      and item['risk'].strip() and item['decision_impact'].strip()]
        if not isinstance(proposal['clarifications'], list):
            raise ValueError()
        proposal['clarifications'] = proposal['clarifications'][:12]
        for item in proposal['clarifications']:
            if (not isinstance(item, dict) or set(item) != {'text', 'evidence_ids'}
                    or not isinstance(item['text'], str)
                    or not isinstance(item['evidence_ids'], list)
                    or any(key not in evidence for key in item['evidence_ids'])):
                raise ValueError()
            item['text'] = item['text'][:700]
            item['evidence_ids'] = item['evidence_ids'][:4]
        proposal['clarifications'] = [item for item in proposal['clarifications'] if item['evidence_ids']]
        if proposal['suggested_letter'] == 'eoi' and proposal['recommendation'] not in {'go', 'conditional_go'}:
            raise ValueError()
        if proposal['suggested_letter'] in {'regret_expertise', 'regret_manpower'} and proposal['recommendation'] != 'no_go':
            raise ValueError()
        if proposal['suggested_letter'] == 'regret_expertise':
            dimension = next(item for item in dimensions if item['dimension'] == 'capability_fit')
            if dimension['assessment'] != 'gap' or not dimension['evidence_ids']:
                proposal['suggested_letter'] = 'user_choice'
        if proposal['suggested_letter'] == 'regret_manpower':
            dimension = next(item for item in dimensions if item['dimension'] == 'resources')
            if dimension['assessment'] != 'gap' or not dimension['evidence_ids']:
                proposal['suggested_letter'] = 'user_choice'
        # There is no approved internal source for Rejlers Abu Dhabi capability,
        # strategy or current resource capacity in this implementation.
        for item in dimensions:
            if item['dimension'] in {'capability_fit', 'strategic_fit', 'resources'}:
                item['assessment'] = 'unknown'
                item['summary'] = 'No approved Rejlers Abu Dhabi source was supplied for this assessment.'
                item['evidence_ids'] = []
        if proposal['suggested_letter'] in {'regret_expertise', 'regret_manpower'}:
            proposal['suggested_letter'] = 'user_choice'
        if proposal['recommendation'] == 'no_go' and proposal['suggested_letter'] not in {'regret_expertise', 'regret_manpower'}:
            proposal['suggested_letter'] = 'user_choice'
    except (KeyError, TypeError, ValueError):
        return {'status': 'unavailable', 'error_code': 'invalid_output'}
    for item in dimensions:
        item['evidence'] = [dict(evidence[key]) for key in item.pop('evidence_ids')]
    for item in proposal['business_risks']:
        item['evidence'] = [dict(evidence[key]) for key in item.pop('evidence_ids')]
    for item in proposal['clarifications']:
        item['evidence'] = [dict(evidence[key]) for key in item.pop('evidence_ids')]
    proposal['rationale_evidence'] = [dict(evidence[key]) for key in proposal.pop('rationale_evidence_ids')]
    return {'status': 'ready', **proposal,
            'coverage': {'source_claims_considered': source_claim_count,
                         'opportunity_fields_considered': record_value_count, 'sources': report.sources.count()},
            'review_note': 'Advisory AI recommendation. Internal capabilities, strategy, resource availability and commercial thresholds were not supplied; review these before deciding.'}


def run_analysis_step(report_id):
    """One durable extraction/provider unit per task; leases recover worker loss."""
    token, now = uuid4(), timezone.now()
    with transaction.atomic():
        report = Report.objects.select_for_update().filter(pk=report_id, status__in=['queued', 'running']).first()
        if (not report or (report.lease_until and report.lease_until > now)
                or (report.next_attempt_at and report.next_attempt_at > now)):
            return False
        report.lease_token, report.lease_until, report.status = token, now + timedelta(minutes=5), 'running'
        report.save()
    try:
        actor = _actor(report.requested_by_id)
        deal = Deal.objects.get(pk=report.opportunity_id)
        authority(deal, actor)
        available = inventory(deal, actor)
        current = snapshot(deal, actor, available)
        if report.dirty or report.snapshot != current:
            with transaction.atomic():
                locked = Report.objects.select_for_update().get(pk=report.pk)
                if locked.lease_token != token:
                    return False
                effective, suppressed = unique_sources(available, locked.suppressed_sources)
                locked.suppressed_sources = suppressed
                keys = {item['key'] for item in effective}
                locked.sources.exclude(source_key__in=keys).delete()
                # Reuse unchanged source/chunk results on later generations.
                for item in effective:
                    row, created = Source.objects.get_or_create(report=locked, source_key=item['key'], defaults={
                        'identity': item['identity'], 'metadata': item['metadata']})
                    if not created and row.identity != item['identity']:
                        row.chunks.all().delete()
                        row.identity, row.metadata, row.status, row.diagnostic, row.references = item['identity'], item['metadata'], 'queued', '', []
                        row.save()
                _collapse_existing_duplicates(locked)
                effective, _ = unique_sources(available, locked.suppressed_sources)
                # Provider changes invalidate AI claims even for identical files.
                old_provider = locked.provider_identity
                provider = digest((email_ai_cache_identity(), VERSION))
                if old_provider and old_provider != provider:
                    locked.sources.all().delete()
                    Source.objects.bulk_create([Source(report=locked, source_key=item['key'], identity=item['identity'], metadata=item['metadata']) for item in effective])
                else:
                    Chunk.objects.filter(source__report=locked, status='failed').update(
                        status='queued', attempts=0, error_code='')
                locked.snapshot, locked.dirty, locked.result, locked.provider_identity = current, False, {}, provider
                locked.save()
            report.refresh_from_db()
        source = report.sources.filter(status='queued').order_by('id').first()
        chunk = None
        final = None
        if source:
            try:
                parts, diagnostic = _extract_source(source, deal, actor)
                extracted = (parts, diagnostic)
            except PermissionDenied:
                raise
            except Exception:
                extracted = ([], 'source_unavailable_or_invalid')
        else:
            chunk = Chunk.objects.filter(source__report=report, status='queued').select_related('source').order_by('id').first()
            if chunk:
                if not email_ai_configuration()['ready']:
                    raise ProviderUnavailable()
                response = analyze_opportunity_sources({'opportunity': deal.deal_name, 'source_name': chunk.source.metadata['name'],
                                                         'location': chunk.location, 'text': chunk.text}, SCHEMA)
                if response['status'] != 'completed':
                    claims = []
                    code = response.get('error_code')
                    chunk_error = code if isinstance(code, str) and code in PROVIDER_ERRORS else 'provider_unavailable'
                else:
                    try:
                        claims, rejected = _verified_claims(response['proposal'], chunk.text)
                        chunk_error = 'invalid_evidence' if rejected else ''
                    except (ValueError, KeyError, TypeError):
                        claims, chunk_error = [], 'invalid_output'
            else:
                final = _compile(report)
                final['manager_review'] = _manager_review(report, deal, final)
        # Nothing generated with stale access/content/configuration is published.
        actor = _actor(report.requested_by_id)
        deal = Deal.objects.get(pk=report.opportunity_id)
        authority(deal, actor)
        latest = snapshot(deal, actor, inventory(deal, actor))
        with transaction.atomic():
            locked = Report.objects.select_for_update().get(pk=report.pk)
            if locked.lease_token != token:
                return False
            if locked.dirty or latest != report.snapshot:
                locked.dirty, locked.status, locked.result = True, 'queued', {}
            elif source:
                parts, diagnostic = extracted
                duplicate = _duplicate_source(source, parts, diagnostic)
                if duplicate:
                    locked.suppressed_sources[source.source_key] = {
                        'identity': source.identity, 'representative': duplicate.source_key,
                        'representative_identity': duplicate.identity,
                    }
                    source.delete()
                else:
                    source.chunks.all().delete()
                    Chunk.objects.bulk_create([Chunk(source=source, ordinal=index, location=location, text=text)
                                              for index, (location, text) in enumerate(parts)])
                    source.status = 'extracted' if parts else 'failed'
                    source.diagnostic = diagnostic
                    source.references = document_references('\n'.join(text for _, text in parts))
                    source.save()
                locked.status = 'queued'
            elif chunk:
                chunk.claims, chunk.error_code = claims, chunk_error
                chunk.attempts += 1
                chunk.status = 'ready' if claims or not chunk_error else 'failed' if chunk.attempts >= 3 else 'queued'
                chunk.save()
                remaining = chunk.source.chunks.exclude(status__in=['ready', 'failed']).exists()
                if not remaining:
                    chunk.source.status = 'partial' if chunk.source.diagnostic or chunk.source.chunks.exclude(error_code='').exists() else 'ready'
                    chunk.source.save(update_fields=['status'])
                locked.status = 'queued'
                locked.error_code = chunk_error
            else:
                locked.result = final
                locked.status = 'partial' if (locked.sources.exclude(status='ready').exists()
                                              or final['manager_review']['status'] != 'ready') else 'ready'
                has_readable = Chunk.objects.filter(source__report=locked, status='ready').exists()
                locked.error_code = '' if has_readable or not locked.sources.exists() else 'no_verified_source_results'
                if locked.error_code:
                    locked.status = 'failed'
            locked.lease_token = locked.lease_until = None
            locked.next_attempt_at = timezone.now() + timedelta(seconds=20 if chunk and chunk_error else 0)
            locked.save()
        return True
    except Exception as error:
        code = 'access_changed' if isinstance(error, PermissionDenied) else 'ai_not_configured' if isinstance(error, ProviderUnavailable) else 'analysis_unavailable'
        Report.objects.filter(pk=report.pk, lease_token=token).update(
            status='blocked' if code == 'access_changed' else 'failed', error_code=code,
            result={}, lease_token=None, lease_until=None, updated_at=timezone.now())
        return False
