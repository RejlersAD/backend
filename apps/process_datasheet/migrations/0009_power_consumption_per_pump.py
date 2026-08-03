# Generated migration for Power Consumption Per Pump fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0008_suction_pressure_calculations'),
    ]

    operations = [
        # Add power_consumption_per_pump JSONField
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='power_consumption_per_pump',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Power consumption per pump calculations'
            ),
        ),
        
        # Add Power Consumption Per Pump fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='hydraulic_power',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Hydraulic power required for pumping in kW',
                max_digits=10,
                null=True,
                verbose_name='Hydraulic Power'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='pump_efficiency',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Pump efficiency percentage',
                max_digits=5,
                null=True,
                verbose_name='Pump Efficiency'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='break_horse_power',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Auto-calculated break horse power in kW',
                max_digits=10,
                null=True,
                verbose_name='Break Horse Power'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='motor_rating',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Motor rating in kW',
                max_digits=10,
                null=True,
                verbose_name='Motor Rating'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='motor_efficiency',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Motor efficiency percentage',
                max_digits=5,
                null=True,
                verbose_name='Motor Efficiency'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='power_consumption',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Auto-calculated total power consumption in kW',
                max_digits=10,
                null=True,
                verbose_name='Power Consumption'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='type_of_motor',
            field=models.CharField(
                blank=True,
                help_text='Motor type (AC Induction, VFD, Synchronous, DC Motor)',
                max_length=50,
                null=True,
                verbose_name='Type of Motor'
            ),
        ),
    ]