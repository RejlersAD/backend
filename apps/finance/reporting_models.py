"""Approved Finance inputs for the executive reporting period."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class ExecutiveFinancePeriod(models.Model):
    month = models.DateField(help_text='First day of the calendar reporting month.')
    currency = models.CharField(max_length=3, validators=[RegexValidator(r'^[A-Z]{3}$')])
    budget_invoiced = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Approved invoicing budget, on the same recorded invoice-amount basis as the workbook.')
    forecast_invoiced = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Approved monthly invoicing forecast. This is not an accounting revenue forecast.')
    recognised_revenue = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='Finance-approved recognised revenue, excluding tax, for the matched cost period.')
    operating_costs = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)], help_text='All matching operating costs in the same currency, scope and period as recognised revenue.')
    actual_through = models.DateField(null=True, blank=True,
        help_text='The inclusive date covered by both recognised revenue and operating costs.')
    status = models.CharField(max_length=12, choices=[('draft', 'Draft'), ('approved', 'Approved')], default='draft')
    source_reference = models.CharField(max_length=500, blank=True,
        help_text='Finance report, workbook version or approval reference. Whole authorised workspace scope.')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['month', 'currency']
        constraints = [models.UniqueConstraint(fields=['month', 'currency'], name='finance_exec_month_currency_unique')]
        verbose_name = 'Executive Finance period'

    def __str__(self):
        return f'{self.month:%Y-%m} {self.currency} ({self.status})'

    def clean(self):
        super().clean()
        errors = {}
        if self.month and self.month.day != 1:
            errors['month'] = 'Use the first day of the reporting month.'
        values = [self.budget_invoiced, self.forecast_invoiced, self.recognised_revenue, self.operating_costs]
        if self.status == 'approved':
            if not self.source_reference.strip():
                errors['source_reference'] = 'An approved Finance source reference is required.'
            if not self.approved_by_id:
                errors['approved_by'] = 'Record the Finance approver.'
            if not self.approved_at:
                errors['approved_at'] = 'Record when Finance approved these figures.'
            elif self.approved_at > timezone.now():
                errors['approved_at'] = 'Approval cannot be in the future.'
            if all(value is None for value in values):
                errors['status'] = 'At least one approved amount is required.'
        has_revenue = self.recognised_revenue is not None
        has_costs = self.operating_costs is not None
        if has_revenue != has_costs:
            errors['operating_costs'] = 'Supply both recognised revenue and matching operating costs, or leave both blank.'
        if has_revenue and has_costs:
            if not self.actual_through:
                errors['actual_through'] = 'Record the date covered by both actual figures.'
            elif self.month and self.actual_through.replace(day=1) != self.month:
                errors['actual_through'] = 'Actual figures must cover the selected reporting month.'
            elif self.actual_through > timezone.localdate():
                errors['actual_through'] = 'Actual figures cannot cover a future date.'
        elif self.actual_through:
            errors['actual_through'] = 'Only provide this date with matched actual revenue and costs.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.currency = self.currency.strip().upper()
        self.full_clean()
        return super().save(*args, **kwargs)
