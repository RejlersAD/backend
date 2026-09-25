"""Reviewed source labels/signers; never a RADAI approval assignment or decision."""

from copy import deepcopy

from django.utils import timezone
from rest_framework.exceptions import APIException

from .pr_review_display import is_richa_name, unknown_approver_name
from .pr_pdf_semantics import approval_role


REVIEW_UNSET = object()
REVIEW_KEY = 'source_approval_review'
LABEL_ROLES = frozenset({'pm', 'moe', 'mop', 'vp'})


class SourceApprovalReviewError(ValueError):
    pass


class SourceApprovalReviewConflict(APIException):
    status_code = 409
    default_code = 'stale_source_approval_review'

    def __init__(self):
        super().__init__({
            'code': self.default_code,
            'error': 'The saved source signatory review changed. Preview the PDF again before saving your review.',
        })


def empty_source_approval_review():
    return {'approval_labels': {}, 'additional_approver': None}


def _text(value, label, limit):
    if not isinstance(value, str):
        raise SourceApprovalReviewError(f'{label} must be text.')
    value = value.strip()
    if len(value) > limit:
        raise SourceApprovalReviewError(f'{label} must contain at most {limit} characters.')
    return value


def normalize_source_approval_review(value):
    if not isinstance(value, dict) or set(value) - {'approval_labels', 'additional_approver', 'approver_notes'}:
        raise SourceApprovalReviewError('Source signatory review contains unsupported fields.')
    labels = value.get('approval_labels', {})
    if not isinstance(labels, dict) or set(labels) - LABEL_ROLES:
        raise SourceApprovalReviewError('Source approval labels must use only pm, moe, mop and vp.')
    normalized_labels = {}
    for role, label in labels.items():
        label = _text(label, 'Source approval label', 20)
        if label:
            normalized_labels[role] = label

    additional = value.get('additional_approver')
    normalized_additional = None
    if additional is not None:
        if not isinstance(additional, dict) or set(additional) - {'name', 'approval_label', 'signature_verified', 'special_note'}:
            raise SourceApprovalReviewError('Additional approver contains unsupported fields.')
        name = _text(additional.get('name', ''), 'Additional approver name', 200)
        label = _text(additional.get('approval_label', ''), 'Additional approver label', 20)
        note = _text(additional.get('special_note', ''), 'Additional approver special note', 2000)
        verified = additional.get('signature_verified', REVIEW_UNSET)
        if verified is not REVIEW_UNSET and type(verified) is not bool:
            raise SourceApprovalReviewError('Additional signature verification must be an explicit Boolean.')
        if name or label or note or verified is True:
            if not name:
                raise SourceApprovalReviewError('Enter the additional approver name before recording its label or signature.')
            if verified is REVIEW_UNSET:
                raise SourceApprovalReviewError('Additional signature verification must be an explicit Boolean.')
            normalized_additional = {'name': name, 'approval_label': label, 'signature_verified': verified}
            if note:
                normalized_additional['special_note'] = note
    notes = value.get('approver_notes', {})
    if not isinstance(notes, dict) or set(notes) - LABEL_ROLES:
        raise SourceApprovalReviewError('Source approver notes must use only pm, moe, mop and vp.')
    notes = {role: text for role, note in notes.items()
             if (text := _text(note, 'Source approver special note', 2000))}
    result = {'approval_labels': normalized_labels, 'additional_approver': normalized_additional}
    if notes:
        result['approver_notes'] = notes
    return result


def normalize_source_approval_submission(review=REVIEW_UNSET, expected=REVIEW_UNSET):
    if review is REVIEW_UNSET:
        if expected is not REVIEW_UNSET:
            raise SourceApprovalReviewError('An expected source review must accompany the reviewed signatory values.')
        return review, expected
    if expected is REVIEW_UNSET:
        raise SourceApprovalReviewError('The expected source signatory review is required. Preview the PDF again.')
    return normalize_source_approval_review(review), normalize_source_approval_review(expected)


def _stored_envelope(metadata):
    evidence = (metadata or {}).get('signed_approval_evidence') or {}
    envelope = evidence.get(REVIEW_KEY)
    if envelope is None:
        return None
    if not isinstance(envelope, dict) or not isinstance(envelope.get('history', []), list):
        raise SourceApprovalReviewError('The saved source signatory review needs reconciliation before editing.')
    normalize_source_approval_review(envelope.get('review'))
    return envelope


def _same_source(metadata, envelope, digest):
    verification = (metadata or {}).get('signed_document_verification') or {}
    return bool(envelope and verification.get('document_sha256') == digest
                and envelope.get('document_sha256') == digest)


def review_for_approver_names(review, names=None):
    projected = deepcopy(review)
    for role, name in (names or {}).items():
        if role in LABEL_ROLES and is_richa_name(name):
            projected['approval_labels'][role] = '0'
    additional = projected.get('additional_approver')
    if additional and is_richa_name(additional.get('name')):
        additional['approval_label'] = '0'
    return projected


def _recorded_names(metadata):
    evidence = (metadata or {}).get('signed_approval_evidence') or {}
    verification = (metadata or {}).get('signed_document_verification') or {}
    row_names = {approval_role(row.get('role_key') or row.get('role', '')): row.get('user_name', '')
                 for row in verification.get('source_approval_rows') or [] if isinstance(row, dict)}
    return {**row_names, **(evidence.get('approver_names') or {}), **(evidence.get('reviewed_approver_names') or {})}


def source_approval_review_from_metadata(metadata, digest):
    envelope = _stored_envelope(metadata)
    if not _same_source(metadata, envelope, digest):
        current_digest = ((metadata or {}).get('signed_document_verification') or {}).get('document_sha256')
        return review_for_approver_names(empty_source_approval_review(), _recorded_names(metadata)) if digest and current_digest == digest else empty_source_approval_review()
    return review_for_approver_names(normalize_source_approval_review(envelope['review']), _recorded_names(metadata))


def prepare_source_approval_review(metadata, digest, actor, review=REVIEW_UNSET, expected=REVIEW_UNSET, *, source_approver_names=None):
    """Called under the import's PR row lock, before any record/storage mutation."""
    review, expected = normalize_source_approval_submission(review, expected)
    envelope = _stored_envelope(metadata)
    same_source = _same_source(metadata, envelope, digest)
    same_document = ((metadata or {}).get('signed_document_verification') or {}).get('document_sha256') == digest
    names = source_approver_names if source_approver_names is not None else _recorded_names(metadata) if same_document else {}
    current = review_for_approver_names(source_approval_review_from_metadata(metadata, digest), names)
    if review is not REVIEW_UNSET:
        review = review_for_approver_names(review, names)
    if review is not REVIEW_UNSET and expected != current and review != current:
        raise SourceApprovalReviewConflict()
    desired = current if review is REVIEW_UNSET else review
    if same_document:
        verification = (metadata or {}).get('signed_document_verification') or {}
        for row in verification.get('source_approval_rows') or []:
            role = approval_role(row.get('role_key') or row.get('role', '')) if isinstance(row, dict) else None
            if role in LABEL_ROLES and unknown_approver_name(names.get(role, row.get('user_name', ''))) and (
                desired['approval_labels'].get(role, '') != current['approval_labels'].get(role, '')
            ) and not desired.get('approver_notes', {}).get(role):
                raise SourceApprovalReviewError('Enter a Special note explaining the unknown approver Level correction.')
    previous_additional = current.get('additional_approver')
    next_additional = desired.get('additional_approver')
    if previous_additional and next_additional and any(
        previous_additional.get(key, '') != next_additional.get(key, '') for key in ('name', 'approval_label')
    ) and not next_additional.get('special_note'):
        raise SourceApprovalReviewError('Enter a Special note explaining the Additional approver name or Level correction.')
    if same_source and desired == current:
        return desired, deepcopy(envelope)
    if envelope is None and desired == current:
        return desired, None

    now = timezone.now().isoformat()
    history = deepcopy(envelope.get('history', [])) if envelope else []
    previous = normalize_source_approval_review(envelope['review']) if envelope else empty_source_approval_review()
    history.append({
        'document_sha256': digest,
        'previous_document_sha256': envelope.get('document_sha256') if envelope else None,
        'before': previous, 'after': deepcopy(desired),
        'reviewed_by_id': str(actor.pk), 'reviewed_at': now,
    })
    return desired, {
        'review': deepcopy(desired), 'document_sha256': digest,
        'reviewed_by_id': str(actor.pk), 'reviewed_at': now, 'history': history,
    }
