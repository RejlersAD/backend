"""Concurrency-safe Purchase Requisition number allocation."""

import re

from django.db import transaction
from django.utils import timezone

from ..models import ProcurementNumberSequence, PurchaseRequisition


PR_NUMBER_PATTERN = re.compile(r'^RAD-(GEN|PRJ)-PR-(\d+)_(\d{4})$')


class RequisitionNumberService:
    TYPE_PREFIXES = {'general': 'GEN', 'project': 'PRJ'}

    @classmethod
    def _largest_existing_value(cls, prefix, year):
        numbers = PurchaseRequisition.objects.filter(
            pr_number__startswith=f'RAD-{prefix}-PR-',
            pr_number__endswith=f'_{year}',
        ).values_list('pr_number', flat=True)
        largest = 0
        for number in numbers:
            match = PR_NUMBER_PATTERN.match(str(number))
            if match and match.group(1) == prefix and int(match.group(3)) == year:
                largest = max(largest, int(match.group(2)))
        return largest

    @classmethod
    @transaction.atomic
    def next_number(cls, requisition_type='project', year=None):
        """Allocate a number while holding the scoped sequence row lock."""
        prefix = cls.TYPE_PREFIXES.get(requisition_type, 'PRJ')
        year = int(year or timezone.localdate().year)
        sequence, _ = ProcurementNumberSequence.objects.select_for_update().get_or_create(
            document_type='PR',
            prefix=prefix,
            year=year,
            defaults={'last_value': 0},
        )
        sequence.last_value = max(
            sequence.last_value,
            cls._largest_existing_value(prefix, year),
        ) + 1
        sequence.save(update_fields=['last_value', 'updated_at'])
        return f'RAD-{prefix}-PR-{sequence.last_value:04d}_{year}'
