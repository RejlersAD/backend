"""
Create PIDVLegendSheet model — stores uploaded legend sheets and AI extractions.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.pid_verification.models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0004_add_per_project_legend'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PIDVLegendSheet',
            fields=[
                ('id',            models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('legend_id',     models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
                ('file_name',     models.CharField(max_length=512)),
                ('original_file', models.FileField(
                    blank=True,
                    max_length=500,
                    null=True,
                    upload_to=apps.pid_verification.models._legend_upload_path,
                )),
                ('s3_path',       models.CharField(blank=True, max_length=1024)),
                ('status',        models.CharField(
                    choices=[
                        ('pending',    'Pending'),
                        ('processing', 'Processing'),
                        ('completed',  'Completed'),
                        ('failed',     'Failed'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('error_message', models.TextField(blank=True)),
                ('extracted_data', models.JSONField(
                    blank=True,
                    null=True,
                    help_text='Structured legend data extracted by AI (see PIDVLegendSheet docstring for schema)',
                )),
                ('created_at',    models.DateTimeField(auto_now_add=True)),
                ('updated_at',    models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='legend_sheets',
                    to='pid_verification.pidvproject',
                )),
                ('uploaded_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pid_legend_sheets',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'pidv_legend_sheets',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['legend_id'],        name='pidv_ls_legend_id_idx'),
                    models.Index(fields=['project', 'status'], name='pidv_ls_proj_status_idx'),
                ],
            },
        ),
    ]
