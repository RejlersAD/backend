"""Explicit PR project references, separate from inferred project ownership."""

from copy import deepcopy

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .requisition_concurrency import RequisitionTimestampField, check_requisition_precondition


def normalize_project_references(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValidationError({'project_number': 'Enter comma-separated project references as text.'})
    references, seen = [], set()
    for item in value.split(','):
        item = item.strip()
        if item and item.casefold() not in seen:
            references.append(item)
            seen.add(item.casefold())
    if len(', '.join(references)) > 200:
        raise ValidationError({'project_number': 'Project references must contain at most 200 characters.'})
    return references


def project_details_for_references(references, existing=None):
    details = existing if isinstance(existing, list) else []
    by_code = {str(row.get('project_number') or row.get('project_code') or row.get('code') or '').strip().casefold(): row
               for row in details if isinstance(row, dict)}
    result = []
    for reference in references:
        row = deepcopy(by_code.get(reference.casefold()) or {})
        row.update(type='project', project_number=reference)
        row.setdefault('value', reference)
        result.append(row)
    result.extend(deepcopy(row) for row in details if isinstance(row, dict) and (
        row.get('type') == 'internal' or not (row.get('project_number') or row.get('project_code') or row.get('code'))
    ))
    return result


def apply_project_references(pr, references):
    from .project_relationships import resolve_requisition_enterprise_project

    pr.project = ', '.join(references)
    pr.project_details = project_details_for_references(references, pr.project_details)
    pr.enterprise_project, _ = resolve_requisition_enterprise_project(
        project=pr.project, project_details=pr.project_details,
    )
    metadata = deepcopy(pr.price_remarks_data or {})
    metadata['project_numbers'] = list(references)
    pr.price_remarks_data = metadata


def prepare_reviewed_project_references(pr, payload, actor, *, document_sha256=None):
    """Mutate only the locked caller's in-memory record; caller saves atomically."""
    from .purchase_order_project_display import requisition_project_numbers

    if not isinstance(payload, dict) or set(payload) != {'project_number', 'expected_updated_at'}:
        raise ValidationError({'reviewed_project_references': 'Supply only project_number and expected_updated_at.'})
    references = normalize_project_references(payload['project_number'])
    try:
        RequisitionTimestampField().run_validation(payload['expected_updated_at'])
    except ValidationError as exc:
        raise ValidationError({'expected_updated_at': exc.detail}) from exc
    previous = requisition_project_numbers(pr)
    if references == previous:
        return references
    check_requisition_precondition(pr, payload['expected_updated_at'])
    apply_project_references(pr, references)
    history = pr.price_remarks_data.setdefault('project_reference_reviews', [])
    if not isinstance(history, list):
        raise ValidationError({'project_number': 'The saved project reference history needs reconciliation.'})
    history.append({'before': previous, 'after': list(references),
                    'document_sha256': document_sha256 or (pr.price_remarks_data.get('signed_document_verification') or {}).get('document_sha256'),
                    'reviewed_by_id': str(actor.pk),
                    'reviewed_at': timezone.now().isoformat()})
    return references
