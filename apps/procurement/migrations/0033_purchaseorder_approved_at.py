from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0032_purchaseorder_buyer_reference_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='approved_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Exact timestamp when final PO approval was recorded',
                null=True,
            ),
        ),
    ]
