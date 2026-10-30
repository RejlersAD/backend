from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0033_client_currency')]

    operations = [
        migrations.AddField(
            model_name='deal',
            name='is_deleted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='deal',
            name='deleted_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddIndex(
            model_name='deal',
            index=models.Index(fields=['is_deleted'], name='sales_deals_is_deleted_idx'),
        ),
    ]