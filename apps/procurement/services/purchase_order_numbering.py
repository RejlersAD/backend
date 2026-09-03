"""Authoritative, concurrency-safe Purchase Order number allocation."""

import re

from django.db import transaction
from django.utils import timezone

from ..models import ProcurementNumberSequence, PurchaseOrder


PO_NUMBER_PATTERN = re.compile(
    r'^RAD-(?P<scope>GEN|PRJ)-PUR-(?P<sequence>\d{4,})_'
    r'(?P<month>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)?'
    r'(?P<year>\d{4})$'
)
PR_NUMBER_PATTERN = re.compile(r'^RAD-(GEN|PRJ)-PR-(\d{4,})_(\d{4})$')


class PurchaseOrderNumberService:
    TYPE_PREFIXES = {'general': 'GEN', 'project': 'PRJ'}

    @classmethod
    def _largest_existing_value(cls, prefix, year):
        numbers = PurchaseOrder.objects.filter(
            po_number__startswith=f'RAD-{prefix}-PUR-',
        ).values_list('po_number', flat=True)
        largest = 0
        for number in numbers:
            match = PO_NUMBER_PATTERN.match(str(number))
            if (
                match
                and match.group('scope') == prefix
                and int(match.group('year')) == year
            ):
                largest = max(largest, int(match.group('sequence')))
        return largest

    @classmethod
    @transaction.atomic
    def next_number(cls, order_type='project', year=None):
        """Allocate the next company-standard PO number under a scoped row lock."""
        prefix = cls.TYPE_PREFIXES.get(order_type, 'PRJ')
        year = int(year or timezone.localdate().year)
        sequence, _ = ProcurementNumberSequence.objects.select_for_update().get_or_create(
            document_type='PO',
            prefix=prefix,
            year=year,
            defaults={'last_value': 0},
        )
        sequence.last_value = max(
            sequence.last_value,
            cls._largest_existing_value(prefix, year),
        ) + 1
        sequence.save(update_fields=['last_value', 'updated_at'])
        return f'RAD-{prefix}-PUR-{sequence.last_value:04d}_{year}'

    @classmethod
    def from_requisition(cls, pr_number):
        """Keep a converted PO aligned with its source PR scope, sequence, and year."""
        match = PR_NUMBER_PATTERN.fullmatch(str(pr_number or '').strip())
        if not match:
            raise ValueError('Source PR number does not follow the company numbering standard.')
        prefix, sequence, year = match.groups()
        return f'RAD-{prefix}-PUR-{int(sequence):04d}_{year}'

    @classmethod
    def next_for_requisition(cls, pr_number):
        """Allocate the next PO number using the source PR scope and year."""
        match = PR_NUMBER_PATTERN.fullmatch(str(pr_number or '').strip())
        if not match:
            raise ValueError('Source PR number does not follow the company numbering standard.')
        prefix, _, year = match.groups()
        order_type = 'general' if prefix == 'GEN' else 'project'
        return cls.next_number(order_type, year=int(year))

    @classmethod
    def verify(cls, po_number, pr_number=None):
        """Verify format and, when applicable, exact correspondence to the source PR."""
        value = str(po_number or '').strip()
        if not PO_NUMBER_PATTERN.fullmatch(value):
            return False, (
                'PO number must use RAD-{GEN|PRJ}-PUR-####_YYYY or '
                'RAD-{GEN|PRJ}-PUR-####_MMMYYYY.'
            )
        if pr_number:
            pr_match = PR_NUMBER_PATTERN.fullmatch(str(pr_number or '').strip())
            if not pr_match:
                return False, 'Source PR number does not follow the company numbering standard.'
            po_match = PO_NUMBER_PATTERN.fullmatch(value)
            if (
                po_match.group('scope') != pr_match.group(1)
                or po_match.group('year') != pr_match.group(3)
            ):
                return False, 'PO and source PR must use the same GEN/PRJ scope and year.'
        return True, 'Verified against the company PO numbering standard.'
