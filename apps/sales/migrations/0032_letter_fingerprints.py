from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0031_analysis_suppressed_sources')]

    operations = [
        migrations.AddField(
            model_name='salesletter',
            name='preparation_fingerprint',
            field=models.CharField(max_length=64, blank=True),
        ),
        migrations.AddField(
            model_name='salesletter',
            name='rendered_fingerprint',
            field=models.CharField(max_length=64, blank=True),
        ),
    ]
