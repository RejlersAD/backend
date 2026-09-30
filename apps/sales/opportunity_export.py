"""CSV projection of an explicit, fully authorized opportunity selection."""

import csv
from datetime import date
from decimal import Decimal
from io import StringIO
import re
from uuid import UUID

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import BID_DECISION_CHOICES, DEAL_STAGES, OPPORTUNITY_TYPE_CHOICES, SERVICE_CATEGORIES


MAX_EXPORT_OPPORTUNITIES = 10000
_FORMULA_START = re.compile(r'^[\s\x00-\x1f\ufeff]*[=+\-@]')
_EXPORT_FIELDS = (
    'id', 'deal_code', 'deal_name', 'client__company_name', 'opportunity_type',
    'service_categories', 'submission_due_date', 'owner__first_name',
    'owner__last_name', 'owner__username', 'owner__email', 'estimated_value',
    'currency', 'probability', 'stage', 'bid_decision', 'next_action',
)


def opportunity_export_ids(payload):
    """Accept a bounded explicit selection; never interpret omission as all rows."""
    if not isinstance(payload, dict) or set(payload) != {'ids'}:
        raise ValidationError({'ids': 'Provide only ids as a comma-separated list of opportunity UUIDs.'})
    value = payload['ids']
    if not isinstance(value, str) or not value.strip() or len(value) > 37 * MAX_EXPORT_OPPORTUNITIES:
        raise ValidationError({'ids': 'Provide between 1 and 10,000 opportunity UUIDs.'})
    parts = value.split(',')
    if len(parts) > MAX_EXPORT_OPPORTUNITIES:
        raise ValidationError({'ids': 'Export at most 10,000 opportunities at a time.'})
    try:
        identifiers = [UUID(part.strip()) for part in parts]
    except (ValueError, AttributeError):
        raise ValidationError({'ids': 'Every selected opportunity must have a valid UUID.'}) from None
    if len(set(identifiers)) != len(identifiers):
        raise ValidationError({'ids': 'Select each opportunity only once.'})
    return identifiers


def _csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, int):
        return str(value)
    text = str(value)
    # Quoting alone does not stop spreadsheet formulas. Preserve the value as
    # text even when whitespace/control characters precede a formula prefix.
    if _FORMULA_START.match(text) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def _service_lines(value):
    categories = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    return '; '.join(
        SERVICE_CATEGORIES.get(category, {}).get('name', category)
        for category in categories if isinstance(category, str) and category
    )


def opportunity_export_response(queryset, identifiers):
    """Validate the entire scoped selection before building any CSV response."""
    records = {
        row['id']: row
        for row in queryset.filter(pk__in=identifiers).select_related(None)
        .prefetch_related(None).values(*_EXPORT_FIELDS)
    }
    if len(records) != len(identifiers):
        raise PermissionDenied(
            'One or more selected opportunities are unavailable or outside your access. '
            'Refresh the register and try again.'
        )

    content = StringIO(newline='')
    content.write('\ufeff')
    writer = csv.writer(content, quoting=csv.QUOTE_ALL)
    writer.writerow((
        'VF code', 'Title', 'Client', 'Type', 'Service line', 'Submission deadline',
        'Owner', 'Estimated value', 'Currency', 'Win probability (%)', 'Stage',
        'Bid decision', 'Next action',
    ))
    types = dict(OPPORTUNITY_TYPE_CHOICES)
    decisions = dict(BID_DECISION_CHOICES)
    for identifier in identifiers:
        row = records[identifier]
        owner = ' '.join(filter(None, (row['owner__first_name'], row['owner__last_name']))).strip()
        owner = owner or row['owner__username'] or row['owner__email'] or ''
        writer.writerow(_csv_cell(value) for value in (
            row['deal_code'], row['deal_name'], row['client__company_name'],
            types.get(row['opportunity_type'], row['opportunity_type']),
            _service_lines(row['service_categories']), row['submission_due_date'],
            owner, row['estimated_value'], row['currency'], row['probability'],
            DEAL_STAGES.get(row['stage'], {}).get('name', row['stage']),
            decisions.get(row['bid_decision'], row['bid_decision']), row['next_action'],
        ))
    response = HttpResponse(content.getvalue(), content_type='text/csv; charset=utf-8')
    filename = f'opportunities-{timezone.localdate().isoformat()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
