"""Actor-scoped source selection and versioned local proposal operations."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from .analysis_models import OpportunityAnalysis as Report
from .bid_preparation import require_editable, visible_deals
from .models import (
    Deal, OpportunityProposalSourceSelection, Quote, QuoteTechnicalProposalVersion,
)
from .opportunity_workspace import require_access
from .proposal_composer import analysis_requirements, compose_technical_proposal
from .proposal_workspace import _validate_content
from .tender_requirements import proposal_source_coverage_is_complete
from .workflow import _audit


def _actor(actor):
    if not actor or not actor.is_active:
        raise PermissionDenied('An active Sales identity is required.')
    return actor


def _deal(actor, deal_id, *, write=False):
    deal = Deal.objects.filter(
        pk=deal_id, pk__in=visible_deals(_actor(actor)),
    ).select_related('client').first()
    if deal is None:
        raise ValidationError({'opportunity': 'This opportunity is unavailable.'})
    require_access(actor, deal, *(['update'] if write else []))
    return deal


def _quote(actor, quote_id, *, write=False, lock=False):
    actions = ('read', 'update') if write else ('read',)
    if not all(module_action_allowed(actor, 'sales_proposals', action) for action in actions):
        raise PermissionDenied('You do not have access to this proposal action.')
    query = Quote.objects.filter(
        pk=quote_id, deal__in=visible_deals(_actor(actor)),
    ).select_related('deal__client', 'client')
    if lock:
        query = query.select_for_update(of=('self',))
    quote = query.first()
    if quote is None:
        raise ValidationError({'proposal': 'This proposal is unavailable.'})
    require_access(actor, quote.deal, *(['update'] if write else []))
    if quote.client_id != quote.deal.client_id:
        raise ValidationError({'proposal': 'The proposal client no longer matches its opportunity.'})
    if write:
        require_editable(quote)
    return quote


def _digest(content):
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _report(deal, actor):
    report = Report.objects.filter(opportunity=deal, requested_by=actor).first()
    if report is None:
        raise ValidationError({'analysis': 'Start and complete document analysis before selecting sources.'})
    if report.dirty:
        raise ValidationError({'analysis': 'The source analysis is stale. Refresh it before proposal generation.'})
    from .opportunity_analysis import inventory, snapshot
    if report.snapshot != snapshot(deal, actor, inventory(deal, actor)):
        raise ValidationError({'analysis': 'The source inventory changed. Refresh analysis before proposal generation.'})
    return report


def source_registry(actor, deal_id, query):
    deal = _deal(actor, deal_id)
    if set(query) - {'offset', 'page_size'}:
        raise ValidationError({'detail': 'Only offset and page_size are supported.'})
    try:
        offset = int(query.get('offset', 0))
        page_size = int(query.get('page_size', 50))
    except (TypeError, ValueError):
        raise ValidationError({'page_size': 'Use integer pagination values.'})
    if offset < 0 or not 1 <= page_size <= 200:
        raise ValidationError({'page_size': 'Select between 1 and 200 rows per page.'})
    report = _report(deal, actor)
    selection = OpportunityProposalSourceSelection.objects.filter(
        opportunity=deal, actor=actor,
    ).first()
    selected = set(selection.source_keys if selection else [])
    sources = report.sources.order_by('id')
    rows = []
    for source in sources[offset:offset + page_size]:
        chunks = source.chunks.values_list('status', flat=True)
        rows.append({
            'key': source.source_key,
            'id': str(source.pk),
            'name': source.metadata.get('name', ''),
            'path': source.metadata.get('source_path') or source.metadata.get('name', ''),
            'folder': source.metadata.get('folder', ''),
            'file_id': source.metadata.get('file_id', ''),
            'provider': source.metadata.get('provider', ''),
            'version': source.metadata.get('version'),
            'sha256': source.metadata.get('sha256', ''),
            'status': source.status,
            'diagnostic': source.diagnostic,
            'chunks_total': len(chunks),
            'chunks_ready': sum(value == 'ready' for value in chunks),
            'selected': source.source_key in selected,
        })
    return {
        'opportunity_id': str(deal.pk),
        'report_status': report.status,
        'snapshot': report.snapshot,
        'total': sources.count(),
        'offset': offset,
        'page_size': page_size,
        'next_offset': offset + page_size if offset + page_size < sources.count() else None,
        'selected_source_keys': sorted(selected),
        'sources': rows,
    }


@transaction.atomic
def save_source_selection(actor, deal_id, payload):
    if not isinstance(payload, dict) or set(payload) != {'source_keys'}:
        raise ValidationError({'source_keys': 'Provide only the selected source_keys list.'})
    values = payload.get('source_keys')
    if not isinstance(values, list) or not values or len(values) > 500:
        raise ValidationError({'source_keys': 'Select between 1 and 500 current sources.'})
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValidationError({'source_keys': 'Every source key must be a string.'})
    values = list(dict.fromkeys(values))
    deal = _deal(actor, deal_id, write=True)
    report = _report(deal, actor)
    available = set(report.sources.values_list('source_key', flat=True))
    missing = sorted(set(values) - available)
    if missing:
        raise ValidationError({'source_keys': f'Select only current registry sources; unknown: {missing}'})
    selection, _ = OpportunityProposalSourceSelection.objects.update_or_create(
        opportunity=deal, actor=actor,
        defaults={'source_keys': values, 'expected_snapshot': report.snapshot},
    )
    return {
        'source_keys': selection.source_keys,
        'snapshot': report.snapshot,
        'selected_count': len(selection.source_keys),
    }


def selected_source_projection(actor, deal_id):
    deal = _deal(actor, deal_id)
    report = _report(deal, actor)
    selection = OpportunityProposalSourceSelection.objects.filter(
        opportunity=deal, actor=actor,
    ).first()
    stale = bool(selection and selection.expected_snapshot != report.snapshot)
    return {
        'source_keys': selection.source_keys if selection else [],
        'snapshot': report.snapshot,
        'stale': stale,
    }


def _selected_matrix(quote, actor):
    selection = OpportunityProposalSourceSelection.objects.filter(
        opportunity=quote.deal, actor=actor,
    ).first()
    if selection is None or not selection.source_keys:
        raise ValidationError({'sources': 'Select current proposal source files before generation.'})
    report = _report(quote.deal, actor)
    if selection.expected_snapshot != report.snapshot:
        raise ValidationError({'sources': 'The selected source snapshot changed. Review the selection again.'})
    source_rows = list(report.sources.filter(source_key__in=selection.source_keys))
    found = {row.source_key for row in source_rows}
    if found != set(selection.source_keys):
        raise ValidationError({'sources': 'One or more selected files are no longer current.'})
    failed = [row.source_key for row in source_rows if row.status not in {'ready', 'extracted'}]
    if failed:
        raise ValidationError({'sources': f'Every selected file must be fully processed: {failed}'})
    matrix = analysis_requirements(quote.deal, actor, source_keys=selection.source_keys)
    coverage = matrix.get('coverage') or {}
    primary = any(
        str(row.metadata.get('folder') or '').casefold() == 'tender'
        and str((row.metadata.get('name') or '')).casefold().find('request for tender') >= 0
        for row in source_rows
    ) or any(item.get('source_type') in {'RFT', 'SOW', 'Specification'} for item in matrix.get('requirements') or [])
    source_coverage = {
        'files_selected': len(source_rows),
        'files_processed': sum(row.status in {'ready', 'extracted'} for row in source_rows),
        'files_failed': sum(row.status == 'failed' for row in source_rows),
        'files_unreadable': sum(row.diagnostic in {'no_readable_text', 'unsupported', 'source_too_large'} for row in source_rows),
        'files_partial': sum(row.status == 'partial' for row in source_rows),
        'mandatory_documents_processed': primary,
        'critical_file_failures': sum(row.status == 'failed' for row in source_rows),
        'source_selection_complete': found == set(selection.source_keys),
        'readable_primary_technical_source': primary,
    }
    matrix['coverage'] = {**coverage, 'proposal_sources': source_coverage}
    matrix['snapshot'] = report.snapshot
    matrix['selected_source_keys'] = selection.source_keys
    matrix['builder_mode'] = 'folder_pipeline'
    from .opportunity_workspace import download_workspace_file
    from .technical_proposal_folder import read_office_source
    by_id = {str(source['source_id']): source for source in matrix.get('all_sources') or []}
    for row in source_rows:
        source = by_id.get(str(row.pk))
        if not source:
            continue
        try:
            stream, filename = download_workspace_file(
                quote.deal, actor, row.metadata.get('folder'), row.metadata.get('file_id'),
            )
            try:
                source.update(read_office_source(stream, filename))
            finally:
                stream.close()
        except ImportError as error:
            source['extraction_error'] = f'optional_reader_missing:{error.name}'
        except Exception:
            source['extraction_error'] = 'source_extraction_failed'
    matrix['document_registry'] = [{
        'key': row.source_key,
        'id': str(row.pk),
        'name': row.metadata.get('name', ''),
        'folder': row.metadata.get('folder', ''),
        'version': row.metadata.get('version'),
        'sha256': row.metadata.get('sha256', ''),
        'source_path': row.metadata.get('source_path') or row.metadata.get('name', ''),
    } for row in source_rows]
    if not proposal_source_coverage_is_complete(source_coverage) or not primary:
        raise ValidationError({'sources': 'Proposal generation requires complete mandatory source coverage.'})
    return matrix


def _serialize_version(row, *, history=False):
    from .proposal_engine_service import config, with_generated_pages
    from .proposal_evidence import evidence_section_keys, replacing_section_keys
    value = {
        'id': str(row.pk),
        'version': row.version,
        'content': row.content if history else with_generated_pages(row.content, row.version),
        # Sections that accept uploaded documents (evidence sections, and Specific HSE Requirements).
        'evidence_sections': evidence_section_keys(config()),
        'evidence_replaces': replacing_section_keys(config()),
        'content_sha256': row.content_sha256,
        'created_at': row.created_at.isoformat(),
        'reason': row.reason,
    }
    if history:
        value.pop('content')
    return value


def workspace(actor, quote_id):
    quote = _quote(actor, quote_id)
    rows = quote.technical_proposal_versions.order_by('-version', '-created_at')
    latest = rows.first()
    from .proposal_engine_service import engine_enabled
    return {
        'quote_id': str(quote.pk),
        'builder': 'standard' if engine_enabled() else 'legacy',
        'current': _serialize_version(latest) if latest else None,
        'history': [_serialize_version(row, history=True) for row in rows[:25]],
    }


@transaction.atomic
def generate_workspace(actor, quote_id, payload):
    if payload not in ({}, None):
        raise ValidationError({'detail': 'Generate the proposal from the explicit saved source selection.'})
    quote = _quote(actor, quote_id, write=True, lock=True)
    matrix = _selected_matrix(quote, actor)
    content = compose_technical_proposal(quote, actor, matrix)
    next_version = (
        QuoteTechnicalProposalVersion.objects.filter(quote=quote)
        .aggregate(value=Max('version'))['value'] or 0
    ) + 1
    content['revision'] = next_version
    row = QuoteTechnicalProposalVersion.objects.create(
        quote=quote, version=next_version, content=content,
        content_sha256=_digest(content), created_by=actor,
        reason='deterministic generation',
    )
    _audit(
        quote.deal, actor, 'technical_proposal_workspace_generated',
        data={'quote_id': str(quote.pk), 'workspace_version': next_version},
    )
    return _serialize_version(row)


@transaction.atomic
def save_workspace(actor, quote_id, payload):
    if not isinstance(payload, dict) or set(payload) != {'expected_version', 'content', 'reason'}:
        raise ValidationError({'content': 'Provide expected_version, content and reason.'})
    quote = _quote(actor, quote_id, write=True, lock=True)
    latest = quote.technical_proposal_versions.order_by('-version').first()
    current_version = latest.version if latest else 0
    if payload.get('expected_version') != current_version:
        raise ValidationError({'expected_version': 'The proposal workspace changed. Refresh before saving.'})
    reason = payload.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValidationError({'reason': 'Provide a review reason of at most 500 characters.'})
    content = _validate_content(payload.get('content'), prior=latest.content if latest else None)
    next_version = current_version + 1
    content['revision'] = next_version
    row = QuoteTechnicalProposalVersion.objects.create(
        quote=quote, version=next_version, content=content,
        content_sha256=_digest(content), created_by=actor, reason=reason.strip(),
    )
    _audit(
        quote.deal, actor, 'technical_proposal_workspace_saved',
        reason=reason.strip(),
        data={'quote_id': str(quote.pk), 'workspace_version': next_version},
    )
    return _serialize_version(row)


@transaction.atomic
def review_workspace(actor, quote_id, payload):
    if not isinstance(payload, dict) or set(payload) != {
        'expected_version', 'section_key', 'approved', 'reason', 'compliance_matrix',
    }:
        raise ValidationError({
            'detail': 'Provide expected_version, section_key, approved, reason and compliance_matrix.',
        })
    latest = QuoteTechnicalProposalVersion.objects.filter(quote_id=quote_id).order_by('-version').first()
    if latest is None:
        raise ValidationError({'workspace': 'Generate the proposal workspace before review.'})
    content = latest.content
    section_key = payload.get('section_key')
    sections = {item['key']: item for item in content.get('sections') or []}
    if section_key not in sections:
        raise ValidationError({'section_key': 'Choose an existing proposal section.'})
    if type(payload.get('approved')) is not bool:
        raise ValidationError({'approved': 'Approval must be true or false.'})
    modified = json.loads(json.dumps(content))
    modified['compliance_matrix'] = payload.get('compliance_matrix')
    modified_section = next(item for item in modified['sections'] if item['key'] == section_key)
    modified_section['approved'] = payload['approved']
    modified_section['status'] = 'approved' if payload['approved'] else 'draft'
    return save_workspace(actor, quote_id, {
        'expected_version': payload.get('expected_version'),
        'content': modified,
        'reason': payload.get('reason'),
    })


@transaction.atomic
def regenerate_section(actor, quote_id, section_key, payload):
    if not isinstance(payload, dict) or set(payload) != {'expected_version', 'reason'}:
        raise ValidationError({'detail': 'Provide expected_version and reason.'})
    latest = QuoteTechnicalProposalVersion.objects.filter(quote_id=quote_id).order_by('-version').first()
    if latest is None:
        raise ValidationError({'workspace': 'Generate the proposal workspace before regeneration.'})
    quote = _quote(actor, quote_id, write=True, lock=True)
    if section_key not in {item.get('key') for item in latest.content.get('sections') or []}:
        raise ValidationError({'section_key': 'Choose an existing proposal section.'})
    generated = compose_technical_proposal(quote, actor, _selected_matrix(quote, actor))
    replacement = next(item for item in generated['sections'] if item['key'] == section_key)
    content = json.loads(json.dumps(latest.content))
    prior_section = next(item for item in content['sections'] if item.get('key') == section_key)
    prior_paragraphs = {}
    for paragraph in prior_section.get('paragraphs') or []:
        for requirement_id in paragraph.get('requirement_ids') or []:
            prior_paragraphs.setdefault(requirement_id, paragraph)
    item_by_id = {item.get('id'): item for item in replacement.get('items') or []}
    for paragraph in replacement.get('paragraphs') or []:
        requirement_id = next(iter(paragraph.get('requirement_ids') or []), None)
        prior = prior_paragraphs.get(requirement_id)
        old_id = paragraph.get('id')
        if prior:
            paragraph['id'] = prior.get('id')
            paragraph['evidence_ids'] = list(prior.get('evidence_ids') or [])
        if old_id in item_by_id:
            item_by_id[old_id]['id'] = paragraph.get('id')
    content['sections'] = [
        replacement if item.get('key') == section_key else item for item in content['sections']
    ]
    replacement['approved'] = False
    replacement['status'] = 'draft'
    return save_workspace(actor, quote_id, {
        'expected_version': payload.get('expected_version'),
        'content': content,
        'reason': payload.get('reason'),
    })


def artifact(actor, quote_id, artifact_name, requested_version):
    if artifact_name not in {
        'proposal', 'registry', 'requirements', 'coverage', 'compliance',
        'traceability', 'preview', 'history', 'build-report', 'inventory',
    }:
        raise ValidationError({'artifact': 'Choose a supported proposal artifact.'})
    quote = _quote(actor, quote_id)
    rows = quote.technical_proposal_versions.order_by('-version')
    if requested_version is not None:
        try:
            requested_version = int(requested_version)
        except (TypeError, ValueError):
            raise ValidationError({'version': 'Use an integer workspace version.'})
        row = rows.filter(version=requested_version).first()
    else:
        row = rows.first()
    if row is None:
        raise ValidationError({'version': 'The requested proposal workspace version was not found.'})
    content = row.content
    mapping = {
        'proposal': content,
        'registry': content.get('document_registry') or [],
        'requirements': content.get('requirement_matrix') or [],
        'coverage': content.get('requirement_coverage') or {},
        'compliance': content.get('compliance_matrix') or [],
        'traceability': content.get('traceability_matrix') or [],
        'build-report': content.get('build_findings') or [],
        'inventory': content.get('build_inventory') or [],
        'history': [_serialize_version(item, history=True) for item in rows[:100]],
    }
    if artifact_name == 'preview':
        mapping['preview'] = {
            'title': content.get('title'),
            'metadata': {
                key: content.get(key) for key in (
                    'client_name', 'opportunity_title', 'opportunity_reference',
                    'proposal_number', 'revision', 'issue_date',
                )
            },
            'sections': content.get('sections') or [],
            'publication_validation': content.get('publication_validation') or {},
        }
    return mapping[artifact_name]


def publication_status(actor, quote_id):
    quote = _quote(actor, quote_id)
    latest = quote.technical_proposal_versions.order_by('-version').first()
    if latest is None:
        return {'exists': False, 'ready': True, 'blockers': []}
    validation = _validate_content(latest.content).get('publication_validation') or {}
    return {'exists': True, **validation, 'version': latest.version}
