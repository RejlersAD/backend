from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0031_purchaseorder_items_table_headers'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='buyer_reference_email',
            field=models.EmailField(
                blank=True,
                help_text='Email fetched from the selected RADAI employee',
                max_length=254,
            ),
        ),
    ]
