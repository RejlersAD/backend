from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0044_resource_productivity')]

    operations = [
        migrations.AddField(model_name='planningriskrecord', name='probability_percent', field=models.DecimalField(
            max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)])),
        migrations.AddField(model_name='planningriskrecord', name='cost_impact', field=models.DecimalField(
            max_digits=16, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])),
        migrations.AddField(model_name='planningriskrecord', name='impact_currency', field=models.CharField(max_length=3, blank=True)),
        migrations.AddField(model_name='planningriskrecord', name='schedule_impact_days', field=models.DecimalField(
            max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])),
        migrations.AddField(model_name='planningriskrecord', name='impact_basis', field=models.TextField(blank=True)),
        migrations.AddField(model_name='planningriskrecord', name='mitigation_due_date', field=models.DateField(null=True, blank=True)),
        migrations.AddField(model_name='planningriskrecord', name='mitigation_status', field=models.CharField(
            max_length=16, default='not_planned', choices=[('not_planned', 'Not Planned'), ('planned', 'Planned'),
                                                         ('in_progress', 'In Progress'), ('completed', 'Completed')])),
        migrations.AddConstraint(model_name='planningriskrecord', constraint=models.CheckConstraint(
            check=models.Q(probability_percent__isnull=True) | models.Q(probability_percent__gte=0, probability_percent__lte=100),
            name='planning_risk_probability_range')),
        migrations.AddConstraint(model_name='planningriskrecord', constraint=models.CheckConstraint(
            check=models.Q(cost_impact__isnull=True) | models.Q(cost_impact__gte=0), name='planning_risk_cost_nonnegative')),
        migrations.AddConstraint(model_name='planningriskrecord', constraint=models.CheckConstraint(
            check=models.Q(schedule_impact_days__isnull=True) | models.Q(schedule_impact_days__gte=0), name='planning_risk_delay_nonnegative')),
    ]
