# Generated manually for Excel Quality Checker models
# Date: 2026-02-26

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('electrical_datasheet', '0002_add_ai_quality_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='UploadedExcelDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='electrical_datasheets/')),
                ('original_filename', models.CharField(max_length=500)),
                ('file_size', models.IntegerField()),
                ('file_hash', models.CharField(db_index=True, max_length=64)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('processing_status', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('parsed_at', models.DateTimeField(blank=True, null=True)),
                ('document_number', models.CharField(blank=True, db_index=True, max_length=200)),
                ('project_name', models.CharField(blank=True, max_length=500)),
                ('equipment_type', models.CharField(blank=True, choices=[('ups', 'UPS'), ('vfd', 'VFD'), ('power_cable', 'Power Cable'), ('control_cable', 'Control Cable'), ('earthing_cable', 'Earthing Cable'), ('ner', 'Neutral Earthing Resistor'), ('motor', 'Motor'), ('transformer', 'Transformer'), ('switchgear', 'Switchgear'), ('other', 'Other')], db_index=True, max_length=50)),
                ('equipment_tag', models.CharField(blank=True, db_index=True, max_length=200)),
                ('revision_number', models.CharField(blank=True, max_length=50)),
                ('issue_date', models.DateField(blank=True, null=True)),
                ('vendor_name', models.CharField(blank=True, max_length=300)),
                ('contractor_name', models.CharField(blank=True, max_length=300)),
                ('parsed_data_json', models.JSONField(blank=True, default=dict)),
                ('cover_sheet_data', models.JSONField(blank=True, default=dict)),
                ('technical_data', models.JSONField(blank=True, default=dict)),
                ('revision_history', models.JSONField(blank=True, default=list)),
                ('validation_score', models.FloatField(blank=True, db_index=True, null=True)),
                ('error_count', models.IntegerField(default=0)),
                ('warning_count', models.IntegerField(default=0)),
                ('info_count', models.IntegerField(default=0)),
                ('validation_summary', models.JSONField(blank=True, default=dict)),
                ('notes', models.TextField(blank=True)),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='uploaded_excel_datasheets', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Uploaded Excel Document',
                'verbose_name_plural': 'Uploaded Excel Documents',
                'db_table': 'electrical_excel_documents',
                'ordering': ['-uploaded_at'],
                'indexes': [
                    models.Index(fields=['equipment_type', 'validation_score'], name='electrical_equiptyp_idx'),
                    models.Index(fields=['uploaded_at', 'processing_status'], name='electrical_uploaded_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ValidationIssue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('severity', models.CharField(choices=[('error', 'Error'), ('warning', 'Warning'), ('info', 'Info')], db_index=True, max_length=20)),
                ('category', models.CharField(db_index=True, max_length=50)),
                ('code', models.CharField(db_index=True, max_length=50)),
                ('message', models.TextField()),
                ('section', models.CharField(blank=True, max_length=200)),
                ('sheet_name', models.CharField(blank=True, max_length=200)),
                ('row_number', models.IntegerField(blank=True, null=True)),
                ('column_name', models.CharField(blank=True, max_length=200)),
                ('field_name', models.CharField(blank=True, max_length=200)),
                ('expected_value', models.TextField(blank=True)),
                ('actual_value', models.TextField(blank=True)),
                ('detected_at', models.DateTimeField(auto_now_add=True)),
                ('acknowledged', models.BooleanField(default=False)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='acknowledged_validation_issues', to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='validation_issues', to='electrical_datasheet.uploadedexceldocument')),
            ],
            options={
                'verbose_name': 'Validation Issue',
                'verbose_name_plural': 'Validation Issues',
                'db_table': 'electrical_validation_issues',
                'ordering': ['severity', 'category', 'code'],
                'indexes': [
                    models.Index(fields=['document', 'severity'], name='electrical_document_idx'),
                    models.Index(fields=['category', 'code'], name='electrical_category_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SheetMetadata',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sheet_name', models.CharField(max_length=200)),
                ('sheet_index', models.IntegerField()),
                ('sheet_type', models.CharField(choices=[('cover', 'Cover Sheet'), ('revision', 'Revision History'), ('holds', 'Holds List'), ('technical', 'Technical Data'), ('other', 'Other')], max_length=50)),
                ('row_count', models.IntegerField()),
                ('column_count', models.IntegerField()),
                ('has_data', models.BooleanField(default=False)),
                ('detected_headers', models.JSONField(blank=True, default=list)),
                ('notes', models.TextField(blank=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sheet_metadata', to='electrical_datasheet.uploadedexceldocument')),
            ],
            options={
                'verbose_name': 'Sheet Metadata',
                'verbose_name_plural': 'Sheet Metadata',
                'db_table': 'electrical_sheet_metadata',
                'ordering': ['sheet_index'],
            },
        ),
        migrations.CreateModel(
            name='ParsedItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sheet_name', models.CharField(max_length=200)),
                ('row_number', models.IntegerField()),
                ('item_type', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True)),
                ('unit', models.CharField(blank=True, max_length=50)),
                ('specified_value', models.TextField(blank=True)),
                ('vendor_value', models.TextField(blank=True)),
                ('compliant', models.BooleanField(blank=True, null=True)),
                ('data_json', models.JSONField(blank=True, default=dict)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parsed_items', to='electrical_datasheet.uploadedexceldocument')),
            ],
            options={
                'verbose_name': 'Parsed Item',
                'verbose_name_plural': 'Parsed Items',
                'db_table': 'electrical_parsed_items',
                'ordering': ['sheet_name', 'row_number'],
            },
        ),
    ]
