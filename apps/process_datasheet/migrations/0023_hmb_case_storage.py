from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('project_organizer', '0001_initial'),
        ('process_datasheet', '0022_hmbmastertemplateprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='hmbmastertemplateprofile',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hmb_master_template_profiles', to='project_organizer.project'),
        ),
        migrations.CreateModel(
            name='HMBCaseImportBatch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('completed', 'Completed'), ('failed', 'Failed')], default='completed', max_length=20)),
                ('source_file_count', models.IntegerField(default=0)),
                ('total_records', models.IntegerField(default=0)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('imported_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hmb_case_import_batches', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hmb_case_import_batches', to='project_organizer.project')),
                ('template_profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='case_import_batches', to='process_datasheet.hmbmastertemplateprofile')),
            ],
            options={
                'db_table': 'process_hmb_case_import_batches',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='HMBCaseRecord',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('case_name', models.CharField(db_index=True, max_length=255)),
                ('source_filename', models.CharField(max_length=255)),
                ('stream_id', models.CharField(db_index=True, max_length=64)),
                ('stream_description', models.TextField(blank=True, default='')),
                ('section_key', models.CharField(db_index=True, max_length=64)),
                ('section_label', models.CharField(max_length=128)),
                ('property_name', models.CharField(db_index=True, max_length=255)),
                ('unit', models.CharField(blank=True, default='', max_length=64)),
                ('value_text', models.TextField(blank=True, default='')),
                ('row_index', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='process_datasheet.hmbcaseimportbatch')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hmb_case_records', to='project_organizer.project')),
                ('template_profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='case_records', to='process_datasheet.hmbmastertemplateprofile')),
            ],
            options={
                'db_table': 'process_hmb_case_records',
                'ordering': ['case_name', 'stream_id', 'row_index'],
            },
        ),
        migrations.AddIndex(
            model_name='hmbcaseimportbatch',
            index=models.Index(fields=['project', '-created_at'], name='process_dat_project_377848_idx'),
        ),
        migrations.AddIndex(
            model_name='hmbcaserecord',
            index=models.Index(fields=['project', 'case_name'], name='process_dat_project_45d54d_idx'),
        ),
        migrations.AddIndex(
            model_name='hmbcaserecord',
            index=models.Index(fields=['project', 'stream_id'], name='process_dat_project_f8c5eb_idx'),
        ),
        migrations.AddIndex(
            model_name='hmbcaserecord',
            index=models.Index(fields=['project', 'property_name'], name='process_dat_project_c8c67e_idx'),
        ),
    ]
