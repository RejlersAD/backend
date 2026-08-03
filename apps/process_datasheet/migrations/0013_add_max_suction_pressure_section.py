# Generated migration for Max Suction Pressure Max Density section fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0012_add_pump_calculation_results'),
    ]

    operations = [
        # Add Max Suction Pressure Max Density section fields
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='suction_vessel_max_op_pressure',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Maximum operating pressure of suction vessel in bar(g)',
                max_digits=10,
                null=True,
                verbose_name='Suction Vessel Max Op. Pressure'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='suction_el_m',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Suction elevation in meters',
                max_digits=10,
                null=True,
                verbose_name='Suction EL,m'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='tl_to_hhll_m',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Tank level to High High Liquid Level in meters',
                max_digits=10,
                null=True,
                verbose_name='TL to HHLL, m'
            ),
        ),
        migrations.AddField(
            model_name='pumpcalculationdata',
            name='max_suction_pressure',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Calculated maximum suction pressure in bar(g)',
                max_digits=10,
                null=True,
                verbose_name='Max Suction Pressure'
            ),
        ),
    ]