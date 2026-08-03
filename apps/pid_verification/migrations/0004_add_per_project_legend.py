"""
Add per-project legend knowledge fields to PIDVProject.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0003_alter_pidvdocument_original_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='pidvproject',
            name='legend_knowledge_data',
            field=models.JSONField(
                blank=True,
                null=True,
                help_text='Extracted legend prefixes specific to this project.',
            ),
        ),
        migrations.AddField(
            model_name='pidvproject',
            name='legend_built_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
            ),
        ),
    ]
