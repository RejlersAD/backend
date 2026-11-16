from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0022_ai_workspace_attachments')]

    operations = [
        migrations.AddField(
            model_name='aiworkspaceattachment', name='extracted_pages',
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name='aiworkspaceattachment', name='extraction_truncated',
            field=models.BooleanField(default=False),
        ),
    ]
