from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0021_remove_pumpcalculationdata_pump_calculation_data_dest_flow_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='HMBMasterTemplateProfile',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('template_name', models.CharField(blank=True, default='', max_length=255)),
                ('source_filename', models.CharField(max_length=255)),
                ('file_sha256', models.CharField(db_index=True, max_length=64)),
                ('sheet_name', models.CharField(blank=True, default='', max_length=120)),
                ('case_title', models.TextField(blank=True, default='')),
                ('stream_count', models.IntegerField(default=0)),
                ('section_count', models.IntegerField(default=0)),
                ('property_row_count', models.IntegerField(default=0)),
                ('analysis_version', models.CharField(default='1.0', max_length=20)),
                ('config_snapshot', models.JSONField(blank=True, default=dict)),
                ('analysis_payload', models.JSONField(blank=True, default=dict)),
                ('normalized_preview', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hmb_master_template_profiles', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'process_hmb_master_template_profiles',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='hmbmastertemplateprofile',
            index=models.Index(fields=['created_by', '-updated_at'], name='process_dat_created_245513_idx'),
        ),
        migrations.AddIndex(
            model_name='hmbmastertemplateprofile',
            index=models.Index(fields=['source_filename'], name='process_dat_source__43a10d_idx'),
        ),
    ]
