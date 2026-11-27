"""Approved talent requisitions become Talent Acquisition openings.

When every approval stage of a talent requisition approves, HR gets an open position with
the requisition's details and, if the requisition names a selected candidate, that candidate
in the pipeline. Running it again for the same requisition changes nothing.
"""
from datetime import date

from .models import RecruitmentCandidate, RecruitmentOpening

REQUISITION_SOURCE = 'requisition'


def _whole_number(value, default=1):
    try:
        return max(1, min(int(str(value).strip()), 9999))
    except (TypeError, ValueError):
        return default


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _text(value, limit=200):
    return str(value or '').strip()[:limit]


def open_requisition_position(enquiry, *, actor=None):
    """Create (once) the opening for an approved requisition. Returns the opening."""
    existing = RecruitmentOpening.objects.filter(requisition=enquiry).first()
    if existing:
        return existing
    details = enquiry.requisition_details if isinstance(enquiry.requisition_details, dict) else {}
    title = _text(details.get('job_title')) or _text(enquiry.subject) or 'Approved requisition'
    department = _text(details.get('req_department')) or _text(enquiry.department) or 'Not assigned'
    opening = RecruitmentOpening.objects.create(
        title=title, department=department, is_open=True, source=REQUISITION_SOURCE,
        requisition=enquiry, positions=_whole_number(details.get('num_positions')),
        hiring_manager=_text(details.get('hiring_manager')), target_join_date=_date(details.get('target_join_date')),
    )
    candidate = _text(details.get('candidate'))
    if candidate:
        # Named in the approved requisition; HR completes contact details in the candidate record.
        RecruitmentCandidate.objects.create(
            name=candidate, email='', position=title, department=department, status='Selected',
            interview_notes=f'Selected candidate named in approved talent requisition {enquiry.reference}.',
        )
    stored = dict(details)
    stored['talent_opening_id'] = opening.pk
    enquiry.requisition_details = stored
    enquiry.save(update_fields=['requisition_details', 'updated_at'])
    from apps.core.models import EnquiryActivity
    EnquiryActivity.objects.create(enquiry=enquiry, actor=actor, action='talent_opening_created',
                                   details={'opening_id': opening.pk, 'candidate': bool(candidate)})
    return opening
