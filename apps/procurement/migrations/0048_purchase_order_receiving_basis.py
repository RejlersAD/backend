from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0047_receipt_delivery_information')]

    operations = [
        migrations.AddField(
            model_name='purchaseorder', name='receiving_basis',
            field=models.JSONField(
                default=dict, blank=True,
                help_text='Audited receiving-only source review; written by the receiving-basis command.',
            ),
        ),
    ]
