# Generated for the AGPS drafting check (services/drafting_check.py)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('electrical_comparison', '0007_electricalcomparisonjob_located_boxes'),
    ]

    operations = [
        migrations.AddField(
            model_name='electricalcomparisonjob',
            name='drafting_results',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
