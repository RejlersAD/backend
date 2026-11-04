"""Shared in-memory evidence store and deterministic orchestration pipeline."""

import re

from .confidence_engine import score_evidence
from .evidence_model import make_record
from .evidence_resolver import resolve_evidence
from .revision_detector import revision_metadata


class EvidenceEngine:
    VERSION = 1

    def __init__(self):
        self._records = []

    def add(self, **values):
        row = make_record(**values)
        if row is None:
            return None
        record = row.as_dict()
        record['confidence'] = score_evidence(record)
        self._records.append(record)
        return record

    def extend(self, records):
        for record in records or ():
            if isinstance(record, dict):
                self.add(**{key: record.get(key) for key in (
                    'field', 'value', 'confidence', 'source_type', 'source_name',
                    'source_location', 'status', 'reason', 'source_id', 'revision', 'revision_date',
                )})

    def resolve(self):
        resolution = resolve_evidence(self._records)
        return {'version': self.VERSION, 'records': list(self._records), **resolution}


def build_unified_evidence(records):
    engine = EvidenceEngine()
    engine.extend(records)
    return engine.resolve()


FIELD_ALIASES = {
    'organization_name': ('organization_name', 'company_name'),
    'customer_name': ('customer_name',), 'project_name': ('project_name',),
    'tender_reference': ('tender_reference',), 'rfq_reference': ('rfq_reference',),
    'pr_reference': ('pr_reference',), 'opportunity_reference': ('opportunity_reference',),
    'scope_summary': ('scope_summary',), 'scope_type': ('scope_type',),
    'estimated_value': ('estimated_value',), 'currency': ('currency',),
    'due_date': ('due_date', 'deadline_date'), 'expected_award_date': ('expected_award_date',),
}


def source_records(fields, *, source_type, source_name, source_id, source_location='', source_text=''):
    """Adapt a specialist reader's field proposals into common source records."""
    if not isinstance(fields, dict):
        return []
    revision = revision_metadata(source_name, source_text)
    records = []
    for target, aliases in FIELD_ALIASES.items():
        value = next((fields.get(key) for key in aliases if fields.get(key)), '')
        if value:
            records.append({
                'field': target, 'value': str(value), 'confidence': 0,
                'source_type': source_type, 'source_name': source_name,
                'source_location': source_location or 'Email body',
                'status': 'likely', 'reason': 'Deterministic field pattern matched source text.',
                'source_id': source_id, 'revision': revision['revision'],
                'revision_date': revision.get('revision_date', ''),
            })
    if fields.get('request_type_code') == 'RFQ' and fields.get('tender_reference'):
        records.append({
            'field': 'rfq_reference', 'value': str(fields['tender_reference']), 'confidence': 0,
            'source_type': source_type, 'source_name': source_name,
            'source_location': source_location or 'Email body', 'status': 'likely',
            'reason': 'The source identifies this as an RFQ reference.', 'source_id': source_id,
            'revision': revision['revision'], 'revision_date': revision.get('revision_date', ''),
        })
    for field, pattern in (
        ('pr_reference', r'\b(?:PR|Purchase\s+Requisition)(?:\s+(?:Reference|Ref(?:erence)?|No\.?|Number))?\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9_./-]{2,})'),
        ('opportunity_reference', r'\bOpportunity(?:\s+(?:Reference|Ref(?:erence)?|No\.?|Number))?\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9_./-]{2,})'),
    ):
        matches = list(re.finditer(pattern, source_text or '', re.I))
        values = {match.group(1).strip('.,;') for match in matches}
        if len(values) == 1:
            records.append({
                'field': field, 'value': next(iter(values)), 'confidence': 0,
                'source_type': source_type, 'source_name': source_name,
                'source_location': source_location or 'Source text', 'status': 'likely',
                'reason': f'Explicit {field.replace("_", " ")} label matched source text.',
                'source_id': source_id, 'revision': revision['revision'],
                'revision_date': revision.get('revision_date', ''),
            })
    return records


def resolved_value(evidence, field):
    """Return a selected field only when the resolver permits its use."""
    selected = (evidence or {}).get('selected', {}).get(field)
    if not isinstance(selected, dict) or selected.get('status') not in {'verified', 'likely'}:
        return ''
    return selected.get('value', '')


def document_evidence_records(name, chunks, source_id):
    """Extract shared business fields from document-reader text chunks."""
    from pathlib import PurePosixPath
    from ..email_extraction import _extract_email_fields

    suffix = PurePosixPath(name or '').suffix.casefold()
    source_type = {
        '.pdf': 'pdf', '.docx': 'docx', '.xlsx': 'xlsx',
        '.eml': 'eml_attachment', '.msg': 'msg_attachment',
    }.get(suffix)
    if not source_type:
        return []
    records = []
    for location, text in chunks or ():
        fields = _extract_email_fields(body_text=text)
        records.extend(source_records(
            fields, source_type=source_type, source_name=name,
            source_id=source_id, source_location=location, source_text=text,
        ))
    return records


def email_evidence_records(subject, body_text, *, source_id, sender_email='', source_name=''):
    """Adapt one email or thread segment using the same deterministic field reader."""
    from ..email_extraction import _extract_email_fields

    fields = _extract_email_fields(subject=subject, body_text=body_text, sender_email=sender_email)
    return source_records(
        fields, source_type='email', source_name=source_name or subject or 'Email message',
        source_id=source_id, source_location='Email body',
        source_text=f'{subject or ""}\n{body_text or ""}',
    )
