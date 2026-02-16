# Generated manually for PumpCalculationData model

import django.contrib.postgres.fields
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('process_datasheet', '0002_datasheetextractionjob_equipment_type_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PumpCalculationData',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('agreement_no', models.CharField(blank=True, help_text='Project agreement number', max_length=100, verbose_name='Agreement No')),
                ('project_no', models.CharField(blank=True, help_text='Unique project identifier', max_length=100, verbose_name='Project No')),
                ('document_no', models.CharField(db_index=True, help_text='Document identification number', max_length=100, unique=True, verbose_name='Document No')),
                ('revision', models.CharField(default='0', help_text='Document revision level', max_length=10, verbose_name='Revision')),
                ('document_class', models.CharField(blank=True, help_text='Document classification', max_length=50, verbose_name='Document Class')),
                ('tag_no', models.CharField(blank=True, db_index=True, help_text='Equipment tag number', max_length=100, verbose_name='Tag No')),
                ('service', models.CharField(blank=True, help_text='Service description', max_length=200, verbose_name='Service')),
                ('motor_classification', models.CharField(blank=True, choices=[('Class I, Division 1', 'Class I, Division 1'), ('Class I, Division 2', 'Class I, Division 2'), ('Class II, Division 1', 'Class II, Division 1'), ('Class II, Division 2', 'Class II, Division 2'), ('Non-Hazardous', 'Non-Hazardous'), ('General Purpose', 'General Purpose')], help_text='Electrical classification for motor', max_length=50, verbose_name='Motor Classification')),
                ('temperature', models.DecimalField(blank=True, decimal_places=2, help_text='Operating temperature (°C)', max_digits=8, null=True, verbose_name='Temperature')),
                ('fluid_viscosity_at_temp', models.DecimalField(blank=True, decimal_places=4, help_text='Fluid viscosity at operating temperature (cP)', max_digits=10, null=True, verbose_name='Fluid Viscosity @ Temp')),
                ('hp', models.DecimalField(blank=True, decimal_places=2, help_text='Horsepower rating', max_digits=10, null=True, verbose_name='HP')),
                ('pump_centerline_elevation', models.DecimalField(blank=True, decimal_places=2, help_text='Pump centerline elevation from grade (m)', max_digits=10, null=True, verbose_name='Pump Central Line Elevation From Grade')),
                ('elevation_source_btl', models.DecimalField(blank=True, decimal_places=2, help_text='Elevation of source bottom tank level from pump centerline (m)', max_digits=10, null=True, verbose_name='Elevation of Source BTL From Pump Central Line')),
                ('general_data', models.JSONField(blank=True, default=dict, help_text='General pump information and specifications')),
                ('liquid_characteristics', models.JSONField(blank=True, default=dict, help_text='Fluid properties and characteristics')),
                ('operating_conditions', models.JSONField(blank=True, default=dict, help_text='Operating parameters and conditions')),
                ('material_design', models.JSONField(blank=True, default=dict, help_text='Materials and design specifications')),
                ('site_utility', models.JSONField(blank=True, default=dict, help_text='Site utility requirements')),
                ('general_notes', models.JSONField(blank=True, default=dict, help_text='General notes and requirements')),
                ('calculation_results', models.JSONField(blank=True, default=dict, help_text='Calculated values and results')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('ifr', 'Issued for Review'), ('ifa', 'Issued for Approval'), ('ifc', 'Issued for Construction'), ('approved', 'Approved')], default='draft', max_length=20)),
                ('source_files', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=500), blank=True, default=list, help_text='Source files used for calculation', size=None)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('checked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='checked_pump_calculations', to=settings.AUTH_USER_MODEL)),
                ('prepared_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='prepared_pump_calculations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Pump Calculation',
                'verbose_name_plural': 'Pump Calculations',
                'db_table': 'pump_calculation_data',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='pumpcalculationdata',
            index=models.Index(fields=['tag_no', 'project_no'], name='pump_calculation_data_tag_no_project_no_idx'),
        ),
        migrations.AddIndex(
            model_name='pumpcalculationdata',
            index=models.Index(fields=['document_no', 'revision'], name='pump_calculation_data_document_no_revision_idx'),
        ),
        migrations.AddIndex(
            model_name='pumpcalculationdata',
            index=models.Index(fields=['status', 'updated_at'], name='pump_calculation_data_status_updated_at_idx'),
        ),
    ]