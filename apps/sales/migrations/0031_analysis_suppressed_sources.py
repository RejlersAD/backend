from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0030_opportunity_analysis'),
    ]

    operations = [
        migrations.AddField(
            model_name='opportunityanalysis',
            name='suppressed_sources',
            field=models.JSONField(default=dict),
        ),
    ]
