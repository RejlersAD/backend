"""Validation boundary for immutable technical-proposal workspace versions."""
from copy import deepcopy

from rest_framework.exceptions import ValidationError

from .local_proposal import FOLDER_SECTION_TITLES, SECTION_TITLES, validate_publication


_TOP_LEVEL_FIELDS = {
    'title', 'template_version', 'client_name', 'opportunity_title',
    'opportunity_reference', 'proposal_number', 'revision', 'issue_date',
    'analysis_status', 'source_snapshot', 'selected_source_keys',
    'requirement_coverage', 'requirement_matrix', 'normalized_requirement_matrix',
    'requirement_normalization', 'compliance_matrix', 'traceability_matrix',
    'evidence_registry', 'publication_validation', 'sections', 'document_registry',
    'amendments', 'requirements_included', 'requirements_truncated',
    'build_meta', 'build_findings', 'build_inventory',
    # Reviewer header/footer lines (effective, and the reviewer's override) for the rendered pages.
    'layout', 'layout_override',
}


def _validate_plain_text(value, field, limit=100_000):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValidationError({field: f'Use plain text of at most {limit} characters.'})
    return value


def _validate_content(content, *, prior=None):
    if not isinstance(content, dict) or set(content) - _TOP_LEVEL_FIELDS:
        raise ValidationError({'content': 'The technical proposal workspace contains unsupported fields.'})
    result = deepcopy(content)
    required = (
        'template_version', 'proposal_number', 'revision', 'analysis_status',
        'source_snapshot', 'selected_source_keys', 'requirement_coverage',
        'requirement_matrix', 'normalized_requirement_matrix',
        'requirement_normalization', 'compliance_matrix', 'traceability_matrix',
        'evidence_registry', 'sections', 'document_registry',
    )
    missing = [field for field in required if field not in result]
    if missing:
        raise ValidationError({'content': f'Missing proposal workspace fields: {", ".join(missing)}'})
    template_version = result.get('template_version')
    if template_version == 'rejlers_authored_v1':
        from .proposal_engine_service import standard_section_titles
        template_titles = standard_section_titles()
    else:
        template_titles = {
            'neutral_13_section_v1': SECTION_TITLES,
            'folder_pipeline_v1': FOLDER_SECTION_TITLES,
        }.get(template_version)
    if template_titles is None:
        raise ValidationError({'template_version': 'Unsupported proposal template version.'})
    sections = result.get('sections')
    if not isinstance(sections, list) or len(sections) != len(template_titles):
        raise ValidationError({
            'sections': f'The proposal must contain all {len(template_titles)} controlled sections.',
        })
    expected = set(template_titles)
    actual = set()
    for section in sections:
        if not isinstance(section, dict):
            raise ValidationError({'sections': 'Each section must be an object.'})
        key, title = section.get('key'), section.get('title')
        if (key, title) not in expected or (key, title) in actual:
            raise ValidationError({'sections': 'Section keys and titles must match the controlled template.'})
        actual.add((key, title))
        _validate_plain_text(title, 'sections.title', 255)
        if not isinstance(section.get('paragraphs'), list):
            raise ValidationError({'sections': 'Each section requires a paragraph list.'})
        for paragraph in section['paragraphs']:
            if not isinstance(paragraph, dict):
                raise ValidationError({'sections': 'Each paragraph must be an object.'})
            _validate_plain_text(paragraph.get('text', ''), 'paragraph.text')
            if not isinstance(paragraph.get('requirement_ids'), list) or not isinstance(
                paragraph.get('evidence_ids'), list,
            ):
                raise ValidationError({'sections': 'Paragraph traceability lists are required.'})
    if actual != expected:
        raise ValidationError({'sections': 'The proposal must preserve the controlled section sequence.'})

    for field in (
        'selected_source_keys', 'requirement_matrix', 'normalized_requirement_matrix',
        'compliance_matrix', 'traceability_matrix', 'document_registry',
    ):
        if not isinstance(result.get(field), list):
            raise ValidationError({field: 'Use a list.'})
    if not isinstance(result.get('evidence_registry'), dict):
        raise ValidationError({'evidence_registry': 'Use an object keyed by evidence ID.'})
    if prior is not None:
        immutable = (
            'proposal_number', 'revision', 'source_snapshot', 'selected_source_keys',
            'requirement_matrix', 'normalized_requirement_matrix', 'requirement_normalization',
            'requirement_coverage', 'document_registry', 'build_meta',
            'build_findings', 'build_inventory',
        )
        for field in immutable:
            if result.get(field) != prior.get(field):
                raise ValidationError({field: 'Regenerate a new proposal revision for source changes.'})
        old_compliance = {
            item.get('requirement_id'): item
            for item in prior.get('compliance_matrix') or []
            if isinstance(item, dict)
        }
        new_sections = {item['key']: item for item in sections}
        for item in result.get('compliance_matrix') or []:
            previous = old_compliance.get(item.get('requirement_id'))
            if previous is not None and item != previous:
                affected = {
                    value for value in (
                        previous.get('proposal_section'), item.get('proposal_section')
                    ) if value
                }
                if any(new_sections.get(key, {}).get('approved') for key in affected):
                    raise ValidationError(
                        {'compliance_matrix': 'Compliance changes require re-approval of every affected section.'}
                    )

    requirement_ids = {
        item.get('id') for item in result['requirement_matrix']
        if isinstance(item, dict) and item.get('id')
    }
    evidence_ids = set(result['evidence_registry'])
    result['publication_validation'] = validate_publication(
        sections,
        result.get('compliance_matrix') or [],
        result.get('traceability_matrix') or [],
        requirement_ids=requirement_ids,
        evidence_ids=evidence_ids,
        requirements=result['requirement_matrix'],
        build_findings=result.get('build_findings'),
        section_titles=template_titles,
    )
    return result
