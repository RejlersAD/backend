from django.db import migrations, models


def preserve_private_attachment_evidence(apps, schema_editor):
    if apps.get_model('sales', 'OpportunityWorkspaceUpload').objects.using(schema_editor.connection.alias).filter(provider='radai').exists():
        raise RuntimeError('Private opportunity attachments exist. Preserve storage identity with a forward fix; reversal would orphan private files.')


class Migration(migrations.Migration):
    dependencies = [('sales', '0012_opportunity_workspace')]
    operations = [
        migrations.AddField(model_name='opportunityworkspaceupload', name='provider', field=models.CharField(default='sharepoint', max_length=20)),
        migrations.AddField(model_name='opportunityworkspaceupload', name='storage_name', field=models.CharField(blank=True, max_length=1024)),
        migrations.AddField(model_name='opportunityworkspaceupload', name='storage_fingerprint', field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name='opportunityworkspaceupload', name='mime_type', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='opportunityworkspaceupload', name='normalized_name', field=models.CharField(max_length=400, null=True)),
        migrations.AddConstraint(model_name='opportunityworkspaceupload', constraint=models.UniqueConstraint(
            fields=('workspace', 'folder_key', 'normalized_name'), condition=models.Q(provider='radai'), name='sales_private_attachment_name')),
        migrations.RunPython(migrations.RunPython.noop, preserve_private_attachment_evidence),
    ]
