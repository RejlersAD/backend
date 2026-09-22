import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('process_datasheet', '0024_hmbcaserecord_source_stream_id'),
        ('project_organizer', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HMBSourceUpload',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('upload_kind', models.CharField(choices=[('master_template', 'Master template'), ('case_file', 'Case file')], db_index=True, max_length=24)),
                ('original_filename', models.CharField(max_length=255)),
                ('storage_key', models.CharField(max_length=1024, unique=True)),
                ('file_sha256', models.CharField(db_index=True, max_length=64)),
                ('size_bytes', models.BigIntegerField(default=0)),
                ('content_type', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('analyzed', 'Analyzed'), ('imported', 'Imported')], db_index=True, default='analyzed', max_length=20)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('imported_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('import_batch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='source_uploads', to='process_datasheet.hmbcaseimportbatch')),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hmb_source_uploads', to='project_organizer.project')),
                ('template_profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='source_uploads', to='process_datasheet.hmbmastertemplateprofile')),
                ('uploaded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hmb_source_uploads', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'process_hmb_source_uploads',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='hmbsourceupload',
            index=models.Index(fields=['project', '-created_at'], name='hmb_project_created_idx'),
        ),
        migrations.AddIndex(
            model_name='hmbsourceupload',
            index=models.Index(fields=['template_profile', 'status'], name='hmb_template_status_idx'),
        ),
    ]