import uuid
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('rbac', '0058_corporate_career_levels')]
    operations = [migrations.CreateModel(
        name='MonthlyChampionPublication',
        fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('period_year', models.PositiveSmallIntegerField()),
            ('period_month', models.PositiveSmallIntegerField()),
            ('published_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('reviewer_id', models.CharField(max_length=64)),
            ('reviewer_name', models.CharField(max_length=254)),
            ('reason', models.TextField()),
            ('snapshot', models.JSONField(default=dict)),
        ],
        options={'db_table': 'monthly_champion_publication', 'ordering': ['-period_year', '-period_month'],
                 'constraints': [models.UniqueConstraint(fields=('period_year', 'period_month'), name='unique_champion_publication_period')]},
    )]
