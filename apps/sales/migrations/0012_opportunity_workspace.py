import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def refuse_workspace_evidence_loss(apps, schema_editor):
    model = apps.get_model('sales', 'OpportunityWorkspace')
    if model.objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError('Workspace requests or external mappings exist. Retain this schema for a forward fix or restore a verified backup; reversal would erase recovery evidence.')


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sales', '0011_vf_opportunity_registration'),
    ]

    operations = [
        migrations.CreateModel(
            name='OpportunityWorkspace',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('required_action', models.CharField(default='create', max_length=10)),
                ('status', models.CharField(db_index=True, default='not_configured', max_length=20)),
                ('config_fingerprint', models.CharField(blank=True, max_length=64)),
                ('root_item_id', models.CharField(blank=True, max_length=255)),
                ('web_url', models.URLField(blank=True, max_length=2000)),
                ('folders', models.JSONField(default=dict)),
                ('intent', models.JSONField(default=dict)),
                ('error_code', models.CharField(blank=True, max_length=40)),
                ('lease_token', models.UUIDField(null=True)),
                ('lease_until', models.DateTimeField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('opportunity', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='document_workspace', to='sales.deal')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='OpportunityWorkspaceUpload',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('request_id', models.UUIDField()),
                ('folder_key', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=255)),
                ('size', models.PositiveBigIntegerField()),
                ('sha256', models.CharField(max_length=64)),
                ('status', models.CharField(default='uploading', max_length=20)),
                ('result', models.JSONField(default=dict)),
                ('error_code', models.CharField(blank=True, max_length=40)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='uploads', to='sales.opportunityworkspace')),
            ],
        ),
        migrations.AddConstraint(
            model_name='opportunityworkspaceupload',
            constraint=models.UniqueConstraint(fields=('workspace', 'request_id'), name='sales_workspace_upload_request'),
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_workspace_evidence_loss),
    ]
