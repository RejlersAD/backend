from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [('procurement', '0045_receipt_command_evidence')]

    operations = [
        migrations.AlterField(
            model_name='receipt',
            name='receipt_date',
            field=models.DateField(default=timezone.localdate),
        ),
    ]
