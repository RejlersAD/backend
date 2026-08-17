"""Authoritative, concurrency-safe Goods Receipt number allocation."""

import re

from django.db import transaction
from django.utils import timezone

from ..models import ProcurementNumberSequence, Receipt


RECEIPT_NUMBER_PATTERN = re.compile(r'^RAD-GR-(\d+)_(\d{4})$')


class ReceiptNumberService:
    @classmethod
    def _largest_existing_value(cls, year):
        numbers = Receipt.objects.filter(
            receipt_number__startswith='RAD-GR-',
            receipt_number__endswith=f'_{year}',
        ).values_list('receipt_number', flat=True)
        largest = 0
        for number in numbers:
            match = RECEIPT_NUMBER_PATTERN.fullmatch(str(number))
            if match and int(match.group(2)) == year:
                largest = max(largest, int(match.group(1)))
        return largest

    @classmethod
    @transaction.atomic
    def next_number(cls, year=None):
        year = int(year or timezone.localdate().year)
        sequence, _ = ProcurementNumberSequence.objects.select_for_update().get_or_create(
            document_type='GR',
            prefix='GR',
            year=year,
            defaults={'last_value': 0},
        )
        sequence.last_value = max(
            sequence.last_value,
            cls._largest_existing_value(year),
        ) + 1
        sequence.save(update_fields=['last_value', 'updated_at'])
        return f'RAD-GR-{sequence.last_value:04d}_{year}'
