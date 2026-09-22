"""Immutable workbook facts for reporting, separate from operational invoices."""
from django.db import models
from django.utils import timezone


class ReceivablesSourceSnapshot(models.Model):
    sha256 = models.CharField(max_length=64)
    file_name = models.CharField(max_length=255)
    sheet_name = models.CharField(max_length=128)
    header_row = models.PositiveIntegerField(default=5)
    first_row = models.PositiveIntegerField(default=6)
    last_row = models.PositiveIntegerField()
    row_count = models.PositiveIntegerField()
    imported_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=False)
    reconciliation = models.JSONField(default=dict)

    class Meta:
        ordering = ['-imported_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['sha256', 'sheet_name', 'header_row', 'first_row', 'last_row'],
                                    name='finance_ar_source_identity'),
            models.UniqueConstraint(fields=['is_active'], condition=models.Q(is_active=True),
                                    name='finance_ar_one_active_source'),
        ]


class ReceivablesSourceRow(models.Model):
    snapshot = models.ForeignKey(ReceivablesSourceSnapshot, on_delete=models.CASCADE, related_name='rows')
    row_number = models.PositiveIntegerField()
    invoice_number = models.CharField(max_length=128, db_index=True)
    # Retain previously stored IDs as unverified historical provenance. Source
    # facts do not depend on operational invoice identities being unique.
    register_invoice_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    category = models.CharField(max_length=16, default='external')
    company = models.CharField(max_length=256, blank=True, default='')
    account = models.CharField(max_length=256, blank=True, default='')
    pm = models.CharField(max_length=128, blank=True, default='')
    project_name = models.TextField(blank=True, default='')
    rad_project_no = models.CharField(max_length=128, blank=True, default='')
    project_id = models.CharField(max_length=128, blank=True, default='')
    payment_terms = models.CharField(max_length=128, blank=True, default='')
    remarks = models.TextField(blank=True, default='')
    invoice_date = models.DateField(null=True, blank=True)
    invoice_sent_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    payment_date = models.DateField(null=True, blank=True)
    payment_status = models.CharField(max_length=64, blank=True, default='', db_index=True)
    raw_payment_status = models.CharField(max_length=128, blank=True, default='')
    currency = models.CharField(max_length=3, blank=True, default='')
    currency_status = models.CharField(max_length=20, default='not_recorded')
    balance_currency = models.CharField(max_length=3, blank=True, default='')
    balance_currency_status = models.CharField(max_length=20, default='not_recorded')
    actual_payment_currency = models.CharField(max_length=3, blank=True, default='')
    actual_payment_currency_status = models.CharField(max_length=20, default='not_recorded')
    invoice_amount = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    invoice_amount_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    balance_to_be_received = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    actual_payment_received = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['row_number']
        constraints = [models.UniqueConstraint(fields=['snapshot', 'row_number'],
                                                name='finance_ar_source_row_unique')]
