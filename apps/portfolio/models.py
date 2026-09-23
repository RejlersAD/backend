"""Versioned workbook facts; intentionally independent of operational projects."""
from django.db import models
from django.utils import timezone


class PortfolioSource(models.Model):
    key = models.CharField(max_length=80, unique=True, default='poc')
    active_snapshot = models.ForeignKey('PortfolioSnapshot', null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='+')
    etag = models.CharField(max_length=512, blank=True)
    remote_identity = models.CharField(max_length=1000, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    sync_token = models.UUIDField(null=True, blank=True)
    sync_expires_at = models.DateTimeField(null=True, blank=True)


class PortfolioSnapshot(models.Model):
    source = models.ForeignKey(PortfolioSource, on_delete=models.CASCADE, related_name='snapshots')
    sha256 = models.CharField(max_length=64)
    parser_version = models.CharField(max_length=32)
    file_name = models.CharField(max_length=255)
    reporting_date = models.DateField()
    imported_at = models.DateTimeField(default=timezone.now)
    row_count = models.PositiveIntegerField()
    warnings = models.JSONField(default=list)
    reconciliation = models.JSONField(default=dict)

    class Meta:
        ordering = ['-reporting_date', '-imported_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['source', 'sha256', 'parser_version'],
                                               name='portfolio_source_version_unique')]


class PortfolioRow(models.Model):
    snapshot = models.ForeignKey(PortfolioSnapshot, on_delete=models.CASCADE, related_name='rows')
    project_code = models.CharField(max_length=128, db_index=True)
    subproject_code = models.CharField(max_length=128)
    title = models.TextField()
    pm = models.CharField(max_length=128, blank=True)
    pc = models.CharField(max_length=128, blank=True)
    business_unit = models.CharField(max_length=128, blank=True)
    client = models.CharField(max_length=255, blank=True)
    scope_type = models.CharField(max_length=128, blank=True)
    currency = models.CharField(max_length=16, blank=True)
    source_row = models.PositiveIntegerField()
    include_without_pt = models.BooleanField(null=True)
    include_with_pt = models.BooleanField(null=True)
    contract_value_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    recognized_revenue_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    period_revenue_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    backlog_without_pt_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    backlog_with_pt_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    poc_pct = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    eddr_pct = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    target_margin_pct = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    forecast_margin_pct = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    overclaim_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    ld_exposure_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    prolongation_cost_aed = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    start_date = models.DateField(null=True)
    contractual_finish = models.DateField(null=True)
    forecast_finish = models.DateField(null=True)
    extra = models.JSONField(default=dict)

    class Meta:
        ordering = ['source_row', 'pk']
        constraints = [models.UniqueConstraint(fields=['snapshot', 'project_code', 'subproject_code'],
                                               name='portfolio_row_identity_unique')]
