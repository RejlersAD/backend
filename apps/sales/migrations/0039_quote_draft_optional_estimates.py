from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0038_opportunity_zip_import')]

    operations = [
        migrations.AlterField('quote', 'subtotal', models.DecimalField(max_digits=15, decimal_places=2,
                                                                       null=True, blank=True)),
        migrations.AlterField('quote', 'total_amount', models.DecimalField(max_digits=15, decimal_places=2,
                                                                           null=True, blank=True)),
        migrations.AlterField('quote', 'estimated_cost', models.DecimalField(max_digits=15, decimal_places=2,
                                                                             null=True, blank=True)),
        migrations.AlterField('quote', 'valid_until', models.DateField(null=True, blank=True)),
    ]
