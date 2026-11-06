# Standard Template export (replaces §7.6 / §10.4):
#  - IOListExtraction gains contractor_number + sheet (Stage 3 Document info)
#  - IOListProject gains settings (tag padding, include review sheets)
#  - new IOListExportTemplate (organisation default / project override)
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.instrument_io_workflow.models import get_io_list_document_storage


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0070_iolistextraction_alarmtrip_payload_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='iolistextraction',
            name='contractor_number',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='iolistextraction',
            name='sheet',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='iolistproject',
            name='settings',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='IOListExportTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('file', models.FileField(
                    storage=get_io_list_document_storage,
                    upload_to='instrument_io_workflow/export_templates/')),
                ('original_name', models.CharField(blank=True, default='', max_length=255)),
                ('validation', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('project', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='export_templates',
                    to='instrument_io_workflow.iolistproject')),
                ('uploaded_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
