# Generated manually for MCF calculation fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0013_add_max_suction_pressure_section'),
    ]

    operations = [
        # Add MCF calculation fields (excluding destination_pressure which already exists)
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='pump_minimum_flow',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Pump minimum flow rate', max_digits=10, null=True, verbose_name='Pump Minimum Flow'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='fluid_density_mcf',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Fluid density for MCF calculation', max_digits=10, null=True, verbose_name='Fluid Density'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='pump_discharge_pressure_min_flow',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Pump discharge pressure at minimum flow', max_digits=10, null=True, verbose_name='Pump Discharge Pressure at Min Flow'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='el_destination_pump_cl',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Elevation of destination from pump centerline', max_digits=10, null=True, verbose_name='EL of Destination from Pump C/L'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='mcf_line_friction_losses',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Minimum flow line friction losses', max_digits=10, null=True, verbose_name='MCF Line Friction Losses'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='flow_meter_losses',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Flow meter pressure losses', max_digits=10, null=True, verbose_name='Flow Meter Losses'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='misc_pressure_drop_mcf',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Miscellaneous pressure drop for MCF', max_digits=10, null=True, verbose_name='Misc. Pressure Drop'),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='mcf_cv_pressure_drop',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Calculated MCF control valve pressure drop', max_digits=10, null=True, verbose_name='MCF CV Pressure Drop'),
        ),
    ]