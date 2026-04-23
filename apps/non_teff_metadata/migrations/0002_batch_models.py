"""
Adds the bulk Master Index models: NonTeffBatch and NonTeffBatchItem.

This migration is hand-written to scope only the non_teff_metadata app and
avoid interactive prompts triggered by unrelated apps with pending model
changes.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('non_teff_metadata', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='NonTeffBatch',
            fields=[
                ('batch_id', models.UUIDField(
                    default=uuid.uuid4, editable=False, primary_key=True, serialize=False,
                )),
                ('name', models.CharField(max_length=255)),
                ('plant', models.CharField(blank=True, max_length=64)),
                ('batch_defaults', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft'),
                        ('uploading', 'Uploading'),
                        ('processing', 'Processing'),
                        ('ready', 'Ready for review'),
                        ('exported', 'Exported'),
                        ('failed', 'Failed'),
                    ],
                    default='draft',
                    max_length=20,
                )),
                ('total_files', models.PositiveIntegerField(default=0)),
                ('ready_files', models.PositiveIntegerField(default=0)),
                ('failed_files', models.PositiveIntegerField(default=0)),
                ('storage_prefix', models.CharField(blank=True, max_length=512)),
                ('error_message', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='non_teff_batches',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Non-TEFF Batch',
                'verbose_name_plural': 'Non-TEFF Batches',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='NonTeffBatchItem',
            fields=[
                ('item_id', models.UUIDField(
                    default=uuid.uuid4, editable=False, primary_key=True, serialize=False,
                )),
                ('file_name', models.CharField(max_length=512)),
                ('relative_path', models.CharField(blank=True, max_length=1024)),
                ('storage_key', models.CharField(blank=True, max_length=1024)),
                ('size_bytes', models.BigIntegerField(default=0)),
                ('sha256', models.CharField(blank=True, db_index=True, max_length=64)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('uploaded', 'Uploaded'),
                        ('extracting', 'Extracting'),
                        ('ready', 'Ready'),
                        ('failed', 'Failed'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('fields', models.JSONField(blank=True, default=dict)),
                ('reviewed', models.BooleanField(default=False)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('batch', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='items',
                    to='non_teff_metadata.nonteffbatch',
                )),
            ],
            options={
                'verbose_name': 'Non-TEFF Batch Item',
                'verbose_name_plural': 'Non-TEFF Batch Items',
                'ordering': ['batch', 'file_name'],
                'indexes': [
                    models.Index(fields=['batch', 'status'], name='non_teff_ba_batch_i_idx'),
                ],
            },
        ),
    ]
