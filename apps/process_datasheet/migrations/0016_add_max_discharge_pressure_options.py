# Generated manually for Option for Max Discharge Pressure fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0015_add_max_discharge_pressure_fields'),
    ]

    operations = [
        # Add Option for Max Discharge Pressure fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='maximum_discharge_pressure_option_1',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Calculated maximum discharge pressure using Option 1 formula', max_digits=10, null=True, verbose_name='Maximum Discharge Pressure (Option 1)'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='maximum_discharge_pressure_option_2',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Calculated maximum discharge pressure using Option 2 formula', max_digits=10, null=True, verbose_name='Maximum Discharge Pressure (Option 2)'),
        ),
    ]