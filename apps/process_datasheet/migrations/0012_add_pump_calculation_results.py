# Generated migration for Pump Calculation Results fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0011_npsh_availability'),
    ]

    operations = [
        # Add Pump Calculation Results fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='discharge_pressure',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Calculated discharge pressure in bar(g)',
                max_digits=10,
                null=True,
                verbose_name='Discharge Pressure'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='suction_pressure_result',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Calculated suction pressure in bar(g)',
                max_digits=10,
                null=True,
                verbose_name='Suction Pressure'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='differential_pressure',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Calculated differential pressure (discharge - suction) in bar',
                max_digits=10,
                null=True,
                verbose_name='Differential Pressure'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='differential_head',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Calculated differential head in m',
                max_digits=10,
                null=True,
                verbose_name='Differential Head'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='npsha_result',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Calculated Net Positive Suction Head Available in m',
                max_digits=10,
                null=True,
                verbose_name='NPSHA'
            ),
        ),
    ]