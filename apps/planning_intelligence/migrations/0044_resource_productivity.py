from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0043_delay_analysis')]
    operations = [
        migrations.AddField(model_name='scheduleresource', name='productivity_rate', field=models.DecimalField(
            max_digits=14, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal('0.0001'))],
            help_text='Output quantity produced per one resource unit; unknown when omitted.')),
        migrations.AddField(model_name='scheduleresource', name='productivity_unit', field=models.CharField(
            max_length=32, blank=True, help_text='Output unit, for example m3 or drawings.')),
        migrations.AddField(model_name='activityassignment', name='planned_output_quantity', field=models.DecimalField(
            max_digits=14, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])),
    ]
