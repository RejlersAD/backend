# Generated migration for Discharge Pressure Calculations fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0003_pumpcalculationdata'),
    ]

    operations = [
        # Add Discharge Pressure Calculations fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='destination_description',
            field=models.CharField(default='Cooling Water Tank (06.5- T - 2307)', help_text='Description of destination equipment or location', max_length=300, verbose_name='Destination Description'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='flow_type',
            field=models.CharField(blank=True, choices=[('Max', 'Max'), ('Normal', 'Normal'), ('Min', 'Min')], help_text='Flow type selection', max_length=20, verbose_name='Flow'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='destination_pressure',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Destination pressure in barg', max_digits=10, null=True, verbose_name='Destination Pressure'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='destination_elevation',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Destination elevation from pump centerline in meters', max_digits=10, null=True, verbose_name='Destination EL from Pump C/L'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='line_friction_loss',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Line friction loss in bar', max_digits=10, null=True, verbose_name='Line Friction Loss'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='flow_meter_del_p',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Flow meter differential pressure in bar', max_digits=10, null=True, verbose_name='Flow meter Del P'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='other_losses',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Other pressure losses in bar', max_digits=10, null=True, verbose_name='Other Losses'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='control_valve',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Control valve pressure drop in bar', max_digits=10, null=True, verbose_name='Control Valve'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='misc_item',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Miscellaneous pressure losses in bar', max_digits=10, null=True, verbose_name='Misc Item'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='contingency',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Contingency pressure allowance in bar', max_digits=10, null=True, verbose_name='Contingency'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='total_discharge_pressure',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Auto-calculated total discharge pressure in bar', max_digits=10, null=True, verbose_name='Total Discharge Pressure'),
        ),
        
        # Add database indexes for better performance
        migrations.AddIndex(
            model_name='pumpcalculationdata',
            index=models.Index(fields=['destination_description', 'flow_type'], name='pump_calculation_data_dest_flow_idx'),
        ),
        migrations.AddIndex(
            model_name='pumpcalculationdata',
            index=models.Index(fields=['total_discharge_pressure'], name='pump_calculation_data_total_pressure_idx'),
        ),
    ]