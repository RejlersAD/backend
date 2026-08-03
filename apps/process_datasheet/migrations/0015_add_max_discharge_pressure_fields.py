# Generated manually for Max Discharge Pressure at Max Density fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0014_add_mcf_fields_corrected'),
    ]

    operations = [
        # Add Max Discharge Pressure at Max Density fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='api_610_tolerance_used',
            field=models.CharField(blank=True, help_text='API 610 Tolerance specification used', max_length=100, null=True, verbose_name='API 610 Tolerance used'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='api_tolerance_factor',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='API tolerance factor value', max_digits=10, null=True, verbose_name='API Tolerance factor'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='shut_off_pressure_factor',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Shut off pressure factor', max_digits=10, null=True, verbose_name='Shut off pressure factor'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='shut_off_differential_pressure',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Calculated shut off differential pressure', max_digits=10, null=True, verbose_name='Shut off Differential Pressure'),
        ),
    ]