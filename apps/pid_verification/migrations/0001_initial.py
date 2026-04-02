# Generated migration for apps.pid_verification
from django.conf import settings
from django.db import migrations, models
import apps.pid_verification.models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PIDVDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('file_name', models.CharField(max_length=512)),
                ('s3_path', models.CharField(blank=True, max_length=1024)),
                ('file_hash', models.CharField(db_index=True, help_text='SHA-256 of the raw file – enables deterministic caching', max_length=64)),
                ('original_file', models.FileField(blank=True, null=True, upload_to=apps.pid_verification.models._pid_upload_path)),
                ('status', models.CharField(choices=[('uploaded', 'Uploaded'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed')], default='uploaded', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('excel_s3_url', models.CharField(blank=True, max_length=1024)),
                ('pdf_s3_url', models.CharField(blank=True, max_length=1024)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pid_v_documents', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'pidv_documents',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['document_id'], name='pidv_docs_doc_id_idx'),
                    models.Index(fields=['file_hash'],   name='pidv_docs_hash_idx'),
                    models.Index(fields=['status'],      name='pidv_docs_status_idx'),
                    models.Index(fields=['uploaded_by', '-created_at'], name='pidv_docs_user_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='PIDVDrawing',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('drawing_id', models.CharField(db_index=True, max_length=100)),
                ('title', models.CharField(blank=True, max_length=512)),
                ('page_index', models.PositiveSmallIntegerField(default=0)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='drawings', to='pid_verification.pidvdocument')),
            ],
            options={
                'db_table': 'pidv_drawings',
                'ordering': ['page_index'],
                'unique_together': {('document', 'drawing_id')},
            },
        ),
        migrations.CreateModel(
            name='PIDVFinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sl_no', models.PositiveIntegerField(help_text='Sequential number within the drawing')),
                ('category', models.CharField(choices=[('tag', 'Tag Issues'), ('connectivity', 'Connectivity Issues'), ('valve', 'Valve & Equipment'), ('line_size', 'Line Size'), ('notes', 'Notes & HOLDs')], max_length=20)),
                ('issue_observed', models.TextField()),
                ('action_required', models.TextField()),
                ('evidence', models.TextField(blank=True, help_text='Raw OCR text / location hint')),
                ('direction', models.CharField(blank=True, help_text='Horizontal / Vertical / N/A', max_length=100)),
                ('severity', models.CharField(choices=[('critical', 'Critical'), ('major', 'Major'), ('minor', 'Minor'), ('info', 'Info')], default='major', max_length=10)),
                ('status', models.CharField(choices=[('open', 'Open'), ('reviewed', 'Reviewed'), ('resolved', 'Resolved')], default='open', max_length=10)),
                ('rule_id', models.CharField(blank=True, help_text='Rule that triggered this finding', max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('drawing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='findings', to='pid_verification.pidvdrawing')),
            ],
            options={
                'db_table': 'pidv_findings',
                'ordering': ['drawing', 'sl_no'],
            },
        ),
    ]
