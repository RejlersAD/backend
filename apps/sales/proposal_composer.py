"""Pure deterministic composer for source-traceable technical proposals."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from rest_framework.exceptions import ValidationError

from .local_proposal import SECTION_TITLES
from .tender_requirements import (
    extract_tender_requirements,
    map_capability_evidence,
    normalize_requirements,
    requirement_matrix_is_complete,
)


def _citation(requirement):
    source = requirement.get('source_file') or 'Unknown source'
    pieces = [source]
    if requirement.get('source_version'):
        pieces.append(f'Version {requirement["source_version"]}')
    if requirement.get('page'):
        pieces.append(f'Page {requirement["page"]}')
    elif requirement.get('source_location'):
        pieces.append(str(requirement['source_location']))
    if requirement.get('clause_location'):
        pieces.append(f'Clause {requirement["clause_location"]}')
    return ' | '.join(pieces)


def _evidence_citation(evidence):
    return ' | '.join(str(value) for value in (
        evidence.get('source_file'), evidence.get('source_location')
    ) if value)


def analysis_requirements(deal, actor, offset=0, limit=50, source_keys=None):
    """Project the current processed analysis chunks into a bounded requirement page."""
    from .opportunity_analysis import authority
    from .analysis_models import OpportunityAnalysis as Report

    authority(deal, actor)
    report = Report.objects.filter(opportunity=deal, requested_by=actor).first()
    if not report or report.dirty or report.status not in {'ready', 'partial'}:
        raise ValidationError({'analysis': 'Complete the opportunity document analysis before generation.'})
    sources = []
    selected = set(source_keys or [])
    query = report.sources.prefetch_related('chunks').order_by('pk')
    if selected:
        query = query.filter(source_key__in=selected)
    for source in query:
        chunks = [{
            'location': chunk.location,
            'text': chunk.text,
        } for chunk in source.chunks.order_by('ordinal') if chunk.status == 'ready' and chunk.text]
        sources.append({
            'source_key': source.source_key,
            'source_id': source.pk,
            'source_identity': source.identity,
            'source_name': source.metadata.get('name'),
            'source_version': str(source.metadata.get('version') or ''),
            'folder': source.metadata.get('folder'),
            'source_path': source.metadata.get('source_path') or source.metadata.get('name'),
            'size_mb': round((source.metadata.get('size') or 0) / 1e6, 2),
            'parse_status': 'ready' if source.status in {'ready', 'extracted'} else source.status,
            'parse_reason': source.diagnostic,
            'chunks': chunks,
        })
    requirements, amendments, coverage = extract_tender_requirements(sources)
    mappings, evidence_coverage = map_capability_evidence(requirements, sources)
    page = requirements[offset:offset + limit]
    page_ids = {item['id'] for item in page}
    return {
        'status': 'ready' if requirement_matrix_is_complete(coverage, len(requirements)) else 'blocked',
        'stale': False,
        'requirements': page,
        'capability_matches': [row for row in mappings if row['requirement_id'] in page_ids],
        'coverage': {**coverage, **evidence_coverage},
        'total': len(requirements),
        'next_offset': offset + limit if offset + limit < len(requirements) else None,
        'amendments': amendments if offset == 0 else [],
        'all_sources': sources if offset == 0 else None,
    }


def _all_requirement_pages(quote, actor, analysis):
    matrix = deepcopy(
        analysis if isinstance(analysis, dict) and analysis.get('requirements') is not None
        else analysis_requirements(quote.deal, actor)
    )
    requirements = list(matrix.get('requirements') or [])
    matches = list(matrix.get('capability_matches') or [])
    next_offset = matrix.get('next_offset')
    selected_source_keys = matrix.get('selected_source_keys')
    while next_offset is not None:
        page = analysis_requirements(
            quote.deal, actor, offset=next_offset, source_keys=selected_source_keys,
        )
        requirements.extend(page.get('requirements') or [])
        matches.extend(page.get('capability_matches') or [])
        next_offset = page.get('next_offset')
    matrix['requirements'] = requirements
    matrix['capability_matches'] = matches
    matrix['total'] = len(requirements)
    matrix['next_offset'] = None
    return matrix


def _item(requirement, evidence=None):
    return {
        'id': 'PAR-' + uuid4().hex[:12],
        'text': requirement['requirement_text'],
        'citation': _citation(requirement),
        'requirement_id': requirement['id'],
        'source_id': requirement.get('source_id'),
        'candidate_evidence': [{
            **candidate,
            'citation': _evidence_citation(candidate),
        } for candidate in (evidence or [])],
        'review_status': 'requires_human_review',
    }


def _section_item(text, requirements, matches, *, predicate=None, evidence_types=None):
    selected = [requirement for requirement in requirements if not predicate or predicate(requirement)]
    by_requirement = {row['requirement_id']: row.get('candidate_evidence') or [] for row in matches}
    rows = [_item(requirement, [
        evidence for evidence in by_requirement.get(requirement['id'], [])
        if not evidence_types or evidence.get('evidence_type') in evidence_types
    ]) for requirement in selected]
    if not rows and text:
        fallback = requirements[0] if requirements else None
        fallback_evidence = next((
            row.get('candidate_evidence') or [] for row in matches
            if fallback and row.get('requirement_id') == fallback.get('id')
        ), [])
        rows = [{
            'id': 'PAR-' + uuid4().hex[:12],
            'text': text,
            'citation': _citation(fallback) if fallback else '',
            'requirement_id': fallback.get('id') if fallback else None,
            'candidate_evidence': [{
                **candidate,
                'citation': _evidence_citation(candidate),
            } for candidate in fallback_evidence],
            'review_status': 'requires_human_review',
        }]
    return rows


def compose_technical_proposal(quote, actor, analysis=None):
    matrix = _all_requirement_pages(quote, actor, analysis)
    if matrix.get('builder_mode') == 'folder_pipeline':
        from .technical_proposal_folder import compose_folder_proposal
        return compose_folder_proposal(quote, matrix)
    requirements = matrix.get('requirements') or []
    matches = matrix.get('capability_matches') or []
    coverage = matrix.get('coverage') or {}
    normalized, normalization_audit = normalize_requirements(requirements)
    if matrix.get('status') != 'ready' or not requirement_matrix_is_complete(coverage, len(requirements)):
        raise ValidationError({
            'analysis': 'A complete, 100% source-linked requirement matrix is required for generation.'
        })

    quote_scope = getattr(quote, 'scope', '') or 'Scope wording requires review.'
    items_by_key = {
        'cover': _section_item(
            f'Technical Proposal for {quote.client.company_name}. Opportunity reference: '
            f'{quote.deal.deal_code}. Proposal: {quote.quote_number}, revision {quote.version}.',
            requirements, matches,
        ),
        'requirements': [_item(requirement, next(
            (row.get('candidate_evidence') for row in matches if row.get('requirement_id') == requirement['id']), [],
        )) for requirement in requirements],
        'scope': _section_item(quote_scope, requirements, matches, predicate=lambda row: 'technical' in row.get('requirement_categories', [])),
        'methodology': _section_item(
            'Execution methodology shall be confirmed against the selected tender requirements.',
            requirements, matches, predicate=lambda row: any(
                word in row['requirement_text'].casefold()
                for word in ('method', 'methodology', 'execution', 'approach')
            ),
        ),
        'organization': _section_item(
            'The project organization and resource allocation require human confirmation.',
            requirements, matches, predicate=lambda row: 'personnel' in row['requirement_text'].casefold()
            or 'resource' in row['requirement_text'].casefold(),
        ),
        'controls': _section_item(
            'Project controls, reporting and change-management arrangements require human review.',
            requirements, matches, predicate=lambda row: any(
                word in row['requirement_text'].casefold()
                for word in ('report', 'schedule', 'control', 'progress')
            ),
        ),
        'deliverables': _section_item(
            '\n'.join(str(item) for item in (getattr(quote, 'deliverables', None) or [])),
            requirements, matches, predicate=lambda row: any(
                word in row['requirement_text'].casefold()
                for word in ('deliverable', 'report', 'document', 'submit')
            ),
        ),
        'quality_hse': _section_item(
            'Quality, inspection and HSE obligations require review before approval.',
            requirements, matches, predicate=lambda row: any(
                category in row.get('requirement_categories', []) for category in ('quality', 'hse')
            ),
        ),
        'personnel': _section_item(
            'No CV evidence candidate is confirmed for this proposal revision.',
            requirements, matches, predicate=lambda row: 'personnel' in row.get('requirement_categories', []),
            evidence_types={'personnel', 'certificate'},
        ),
        'experience': _section_item(
            'Relevant company experience remains subject to verification.',
            requirements, matches, predicate=lambda row: 'qualification' in row.get('requirement_categories', []),
            evidence_types={'project_reference', 'certificate'},
        ),
        'subcontractors': _section_item(
            'No subcontractor is proposed unless a reviewed candidate is expressly selected.',
            requirements, matches, predicate=lambda row: 'subcontractor' in row['requirement_text'].casefold(),
            evidence_types={'subcontractor'},
        ),
        'assumptions': _section_item(
            '\n'.join(
                str(item) for item in (
                    (getattr(quote, 'assumptions', None) or [])
                    + (getattr(quote, 'exclusions', None) or [])
                )
            ) or 'No unreviewed assumptions or exclusions have been added.',
            requirements, matches, predicate=lambda row: 'optional' == row.get('mandatory_optional'),
        ),
        'appendices': [
            {
                **candidate,
                'id': 'PAR-' + uuid4().hex[:12],
                'text': candidate.get('excerpt') or 'Candidate evidence requires review.',
                'citation': _evidence_citation(candidate),
                'requirement_id': match.get('requirement_id'),
                'candidate_evidence': [],
                'review_status': 'requires_human_review',
            }
            for match in matches for candidate in (match.get('candidate_evidence') or [])
        ] or [_item(requirement) for requirement in requirements],
    }

    by_requirement = {row['id']: row for row in requirements}
    evidence_registry = {}
    compliance_matrix = []
    for index, match in enumerate(matches):
        requirement = by_requirement[match['requirement_id']]
        evidence = match.get('candidate_evidence') or []
        evidence_ids = []
        for candidate in evidence:
            evidence_id = 'EVD-' + uuid4().hex[:12]
            evidence_ids.append(evidence_id)
            evidence_registry[evidence_id] = {
                'requirement_id': requirement['id'],
                **candidate,
            }
        compliance_matrix.append({
            'requirement_id': requirement['id'],
            'normalized_requirement_id': next(
                item['id'] for item in normalized
                if requirement['id'] in item['original_requirement_ids']
            ),
            'proposal_section': 'requirements',
            'compliance_status': 'Gap' if not evidence else 'Review Required',
            'evidence_reference': evidence_ids,
            'source_references': next(
                item['source_references'] for item in normalized
                if requirement['id'] in item['original_requirement_ids']
            ),
            'review_note': 'Human review and section approval are required.',
        })

    sections = []
    paragraph_ids_by_requirement = {}
    for key, title in SECTION_TITLES:
        items = items_by_key[key]
        paragraphs = []
        for item in items:
            requirement_id = item.get('requirement_id')
            evidence_ids = [
                evidence_id for evidence_id, evidence in evidence_registry.items()
                if evidence.get('requirement_id') == requirement_id
            ]
            if requirement_id:
                paragraph_ids_by_requirement.setdefault(requirement_id, []).append({
                    'paragraph_id': item['id'], 'section_key': key,
                })
            paragraphs.append({
                'id': item['id'],
                'text': item['text'],
                'requirement_ids': [requirement_id] if requirement_id else [],
                'evidence_ids': evidence_ids if evidence_ids else [],
            })
        requirement_ids = list(dict.fromkeys(
            requirement_id for requirement_id in (
                item.get('requirement_id') for item in items
            ) if requirement_id
        ))
        sections.append({
            'key': key,
            'title': title,
            'paragraphs': paragraphs,
            'items': items,
            'requirement_ids': requirement_ids,
            'status': 'draft',
            'approved': False,
        })

    traceability = []
    for requirement in requirements:
        traceability.append({
            'requirement_id': requirement['id'],
            'normalized_requirement_id': next(
                item['id'] for item in normalized
                if requirement['id'] in item['original_requirement_ids']
            ),
            'proposal_paragraphs': paragraph_ids_by_requirement.get(requirement['id'], []),
            'result': 'mapped' if requirement['id'] in paragraph_ids_by_requirement else 'unmapped',
        })
    return {
        'title': f'Technical Proposal - {quote.deal.deal_name}',
        'template_version': 'neutral_13_section_v1',
        'client_name': quote.client.company_name,
        'opportunity_title': quote.deal.deal_name,
        'opportunity_reference': quote.deal.deal_code,
        'proposal_number': quote.quote_number,
        'revision': quote.version,
        'issue_date': str(quote.issue_date or ''),
        'analysis_status': matrix.get('status'),
        'source_snapshot': str(matrix.get('snapshot') or ''),
        'selected_source_keys': list(matrix.get('selected_source_keys') or []),
        'requirement_coverage': coverage,
        'requirement_matrix': requirements,
        'normalized_requirement_matrix': normalized,
        'requirement_normalization': normalization_audit,
        'compliance_matrix': compliance_matrix,
        'traceability_matrix': traceability,
        'evidence_registry': evidence_registry,
        'publication_validation': {'ready': False, 'blockers': ['human_review_required']},
        'sections': sections,
        'document_registry': matrix.get('document_registry') or [],
        'amendments': matrix.get('amendments') or [],
        'requirements_included': len(requirements),
        'requirements_truncated': bool(coverage.get('records_truncated')),
    }
