"""Bounded CSV projection of an explicit, fully authorized proposal selection."""

import csv
from io import StringIO
from uuid import UUID

from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.data_visibility_mixin import build_visibility_filter
from .models import Deal, Quote
from .opportunity_export import _csv_cell, _service_lines


MAX_EXPORT_PROPOSALS = 10000
_EXPORT_FIELDS = (
    'id', 'quote_number', 'version', 'deal__deal_code', 'deal__deal_name',
    'client__company_name', 'status', 'prepared_by__first_name',
    'prepared_by__last_name', 'prepared_by__username', 'prepared_by__email',
    'deal__service_categories', 'deal__submission_due_date', 'valid_until',
    'issue_date', 'total_amount', 'estimated_cost', 'currency',
)


def proposal_export_ids(payload):
    """Omission, duplicates and excessive selections never mean export all."""
    if not isinstance(payload, dict) or set(payload) != {'ids'}:
        raise ValidationError({'ids': 'Provide only ids as a comma-separated list of proposal UUIDs.'})
    value = payload['ids']
    if not isinstance(value, str) or not value.strip() or len(value) > 37 * MAX_EXPORT_PROPOSALS:
        raise ValidationError({'ids': 'Provide between 1 and 10,000 proposal UUIDs.'})
    parts = value.split(',')
    if len(parts) > MAX_EXPORT_PROPOSALS:
        raise ValidationError({'ids': 'Export at most 10,000 proposals at a time.'})
    try:
        identifiers = [UUID(part.strip()) for part in parts]
    except (ValueError, AttributeError):
        raise ValidationError({'ids': 'Every selected proposal must have a valid UUID.'}) from None
    if len(set(identifiers)) != len(identifiers):
        raise ValidationError({'ids': 'Select each proposal only once.'})
    return identifiers


def proposal_export_response(queryset, identifiers, actor):
    """Apply opportunity visibility before constructing any downloadable bytes."""
    visible = Deal.objects.filter(build_visibility_filter(user=actor, module_code='sales', owner_field='owner'))
    records = {
        row['id']: row for row in queryset.filter(
            pk__in=identifiers, deal__in=visible, client_id=F('deal__client_id'),
        ).select_related(None).prefetch_related(None).values(*_EXPORT_FIELDS)
    }
    if len(records) != len(identifiers):
        raise PermissionDenied(
            'One or more selected proposals are unavailable or outside your access. '
            'Refresh the register and try again.'
        )

    content = StringIO(newline='')
    content.write('\ufeff')
    writer = csv.writer(content, quoting=csv.QUOTE_ALL)
    writer.writerow((
        'Proposal', 'Version', 'VF code', 'Title', 'Client', 'Status', 'Owner',
        'Service line', 'Submission deadline', 'Valid until', 'Issue date',
        'Proposed price', 'Estimated cost', 'Currency',
    ))
    statuses = dict(Quote._meta.get_field('status').choices)
    for identifier in identifiers:
        row = records[identifier]
        owner = ' '.join(filter(None, (row['prepared_by__first_name'], row['prepared_by__last_name']))).strip()
        owner = owner or row['prepared_by__username'] or row['prepared_by__email'] or ''
        writer.writerow(_csv_cell(value) for value in (
            row['quote_number'], row['version'], row['deal__deal_code'], row['deal__deal_name'],
            row['client__company_name'], statuses.get(row['status'], row['status']), owner,
            _service_lines(row['deal__service_categories']), row['deal__submission_due_date'],
            row['valid_until'], row['issue_date'], row['total_amount'], row['estimated_cost'], row['currency'],
        ))
    response = HttpResponse(content.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="proposals-{timezone.localdate().isoformat()}.csv"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
