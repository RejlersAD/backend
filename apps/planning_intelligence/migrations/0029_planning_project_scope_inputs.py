from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0028_schedule_control_observation_revisions')]

    operations = [
        migrations.AddField(
            model_name='planningproject', name='scope_summary',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='planningproject', name='exclusions',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='planningproject', name='budgeted_effort_hours',
            field=models.DecimalField(
                max_digits=14, decimal_places=2, null=True, blank=True,
                validators=[django.core.validators.MinValueValidator(Decimal('0'))],
                help_text='Draft planning effort in hours; does not establish an approved control budget.',
            ),
        ),
        migrations.AddConstraint(
            model_name='planningproject',
            constraint=models.CheckConstraint(
                check=models.Q(budgeted_effort_hours__isnull=True) | models.Q(budgeted_effort_hours__gte=0),
                name='plan_project_effort_nonnegative',
            ),
        ),
    ]
