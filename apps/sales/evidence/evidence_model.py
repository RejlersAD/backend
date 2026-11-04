"""Versioned, transport-neutral evidence records."""

from dataclasses import asdict, dataclass
import re


FIELDS = frozenset({
    'customer_name', 'organization_name', 'project_name', 'tender_reference',
    'rfq_reference', 'pr_reference', 'opportunity_reference', 'scope_summary',
    'scope_type', 'estimated_value', 'currency', 'due_date', 'expected_award_date',
})
SOURCE_TYPES = frozenset({
    'email', 'thread', 'pdf', 'docx', 'xlsx', 'eml_attachment', 'msg_attachment',
})
STATUSES = frozenset({'verified', 'likely', 'conflict', 'manual_review_required'})


@dataclass(frozen=True)
class EvidenceRecord:
    field: str
    value: str
    confidence: float
    source_type: str
    source_name: str
    source_location: str
    status: str
    reason: str
    source_id: str = ''
    revision: int = 0
    revision_date: str = ''

    def as_dict(self):
        return asdict(self)


def make_record(*, field, value, confidence, source_type, source_name,
                source_location='', status='likely', reason='', source_id='',
                revision=0, revision_date=''):
    field = str(field or '').strip()
    value = re.sub(r'\s+', ' ', str(value or '')).strip()[:2000]
    if field not in FIELDS or not value:
        return None
    if source_type not in SOURCE_TYPES:
        source_type = 'email'
    try:
        confidence = max(0.0, min(100.0, float(confidence)))
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    if status not in STATUSES:
        status = 'likely'
    try:
        revision = max(0, int(revision))
    except (TypeError, ValueError, OverflowError):
        revision = 0
    return EvidenceRecord(
        field=field, value=value, confidence=confidence, source_type=source_type,
        source_name=str(source_name or 'Unknown source')[:255],
        source_location=str(source_location or 'Unspecified location')[:255],
        status=status, reason=str(reason or 'Extracted from source evidence.')[:500],
        source_id=str(source_id or '')[:255], revision=revision,
        revision_date=str(revision_date or '')[:40],
    )
