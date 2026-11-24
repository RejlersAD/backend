"""Validation rules for local technical proposal workspaces."""
from __future__ import annotations


SECTION_TITLES = (
    ('cover', 'Cover Page & Document Control'),
    ('requirements', 'Understanding of Client Requirements'),
    ('scope', 'Scope of Services'),
    ('methodology', 'Project Execution Methodology'),
    ('organization', 'Organization, Roles & Resources'),
    ('controls', 'Project Controls & Reporting'),
    ('deliverables', 'Deliverables & Submission Register'),
    ('personnel', 'Key Personnel & CV Index'),
    ('quality_hse', 'Quality, HSE & Assurance'),
    ('experience', 'Relevant Experience'),
    ('subcontractors', 'Subcontractors & Specialist Partners'),
    ('appendices', 'Compliance Matrix, Traceability & Appendices'),
    ('assumptions', 'Assumptions, Qualifications & Exclusions'),
)

FOLDER_SECTION_TITLES = (
    ('cover', 'Cover Page & Document Control'),
    ('toc', 'Table of Contents'),
    ('letter', 'Covering Letter'),
    ('qs', 'Qualification Statement'),
    ('experience', 'Previous Experience'),
    ('execution', 'Project Execution Strategy'),
    ('deliverables', 'List of Deliverables'),
    ('organization', 'Organization and Resources'),
    ('personnel', 'Key Personnel'),
    ('project_control', 'Project Control Requirements'),
    ('hse', 'HSE Management'),
    ('subcontractors', 'Proposed Subcontractors'),
    ('bv', 'Business Venture'),
    ('investment', 'Investment Plan'),
    ('tq', 'Technical Query Log'),
    ('annex', 'Annex Index'),
)


def validate_publication(
    sections, compliance, traceability, *, requirement_ids, evidence_ids, requirements,
    build_findings=None, section_titles=None,
):
    blockers = []
    requirement_ids = set(requirement_ids)
    evidence_ids = set(evidence_ids)
    section_titles = section_titles or (
        FOLDER_SECTION_TITLES
        if any(finding.get('code') in {'META_MISSING', 'SECTION_NO_SOURCE'} for finding in (build_findings or []))
        or len(sections) == len(FOLDER_SECTION_TITLES)
        else SECTION_TITLES
    )
    section_keys = {key for key, _ in section_titles}
    by_key = {section.get('key'): section for section in sections if isinstance(section, dict)}

    if set(by_key) != section_keys:
        missing = sorted(section_keys - set(by_key))
        extra = sorted(set(by_key) - section_keys)
        if missing:
            blockers.append(f'proposal_sections_missing:{",".join(missing)}')
        if extra:
            blockers.append(f'proposal_sections_unknown:{",".join(extra)}')

    paragraph_ids = {}
    for key, section in by_key.items():
        if not section.get('approved'):
            blockers.append(f'section_not_approved:{key}')
        for paragraph in section.get('paragraphs') or []:
            paragraph_id = paragraph.get('id')
            if paragraph_id:
                paragraph_ids[(paragraph_id, key)] = paragraph
            if not requirement_ids:
                continue
            reqs = set(paragraph.get('requirement_ids') or [])
            evidence = set(paragraph.get('evidence_ids') or [])
            if not reqs or not evidence or not reqs <= requirement_ids or not evidence <= evidence_ids:
                blockers.append(f'untraceable_paragraph:{paragraph_id or "unknown"}')

    folder_mode = build_findings is not None
    for finding in build_findings or []:
        if finding.get('severity') == 'ERROR':
            blockers.append(f"build_{finding.get('code', 'ERROR')}:{finding.get('message', '')}")
    if folder_mode:
        return {'ready': not blockers, 'blockers': list(dict.fromkeys(blockers))}

    compliance_by_requirement = {
        row.get('requirement_id'): row for row in compliance if isinstance(row, dict)
    }
    traceability_by_requirement = {
        row.get('requirement_id'): row for row in traceability if isinstance(row, dict)
    }
    for requirement in requirements:
        identifier = requirement.get('id')
        compliance_row = compliance_by_requirement.get(identifier)
        if requirement.get('mandatory_optional') == 'mandatory' and (
            not compliance_row or str(compliance_row.get('compliance_status')).casefold() != 'covered'
        ):
            blockers.append(f'mandatory_requirement_not_covered:{identifier}')
        if identifier in requirement_ids and (
            identifier not in traceability_by_requirement
            or traceability_by_requirement[identifier].get('result') != 'mapped'
        ):
            blockers.append(f'requirement_not_mapped:{identifier}')
        links = (traceability_by_requirement.get(identifier) or {}).get('proposal_paragraphs') or []
        if any(
            (link.get('paragraph_id'), link.get('section_key')) not in paragraph_ids
            for link in links
        ):
            blockers.append(f'traceability_target_missing:{identifier}')
    return {'ready': not blockers, 'blockers': list(dict.fromkeys(blockers))}
