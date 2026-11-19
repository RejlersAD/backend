import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('sales', '0037_sales_email_intake_attachments'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name='OpportunityZipImport',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('folder_key', models.CharField(max_length=30, default='tender')),
                ('status', models.CharField(max_length=20, default='queued', db_index=True)),
                ('entries_total', models.PositiveIntegerField(default=0)),
                ('processed', models.PositiveIntegerField(default=0)),
                ('skipped', models.PositiveIntegerField(default=0)),
                ('failed', models.PositiveIntegerField(default=0)),
                ('cursor', models.PositiveIntegerField(default=0)),
                ('errors', models.JSONField(default=list)),
                ('error_code', models.CharField(max_length=60, blank=True)),
                ('lease_token', models.UUIDField(null=True)),
                ('lease_until', models.DateTimeField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('opportunity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='zip_imports', to='sales.deal')),
                ('source_upload', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='zip_import_job', to='sales.opportunityworkspaceupload')),
            ],
        ),
        migrations.AddField(model_name='opportunityworkspaceupload', name='zip_import',
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                    related_name='extracted_uploads', to='sales.opportunityzipimport')),
        migrations.AddField(model_name='opportunityworkspaceupload', name='source_path',
                            field=models.CharField(max_length=1024, blank=True, default='')),
    ]
