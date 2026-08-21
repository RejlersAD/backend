from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0030_purchaseorder_seller_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='items_table_headers',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Editable column headings used by the PO items and pricing tables',
            ),
        ),
    ]
