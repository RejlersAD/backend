from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0029_repair_legacy_pr_insert_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='seller_address',
            field=models.TextField(
                blank=True,
                help_text='Seller office or registered address',
            ),
        ),
    ]
