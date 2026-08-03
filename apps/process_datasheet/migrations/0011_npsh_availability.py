# Generated migration for NPSH Availability fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0009_power_consumption_per_pump'),
    ]

    operations = [
        # Add npsh_availability JSONField
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='npsh_availability',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='NPSH availability calculations and parameters'
            ),
        ),
        
        # Add NPSH Availability fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='suction_pressure_npsh',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Suction pressure for NPSH calculation in bar(g)',
                max_digits=10,
                null=True,
                verbose_name='Suction Pressure (NPSH)'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='vapor_pressure',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Vapor pressure of fluid in bar(g)',
                max_digits=10,
                null=True,
                verbose_name='Vapor Pressure'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='npsha',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Auto-calculated Net Positive Suction Head Available in m',
                max_digits=10,
                null=True,
                verbose_name='NPSHA'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='safety_margin_npsha',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Safety margin for NPSHA calculation in m',
                max_digits=10,
                null=True,
                verbose_name='Safety Margin for NPSHA'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='npsha_with_safety_margin',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Auto-calculated NPSHA with safety margin in m',
                max_digits=10,
                null=True,
                verbose_name='NPSHA (With Safety Margin)'
            ),
        ),
    ]