from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('sales', '0035_opportunity_remove_deal_sales_deals_is_deleted_idx')]

    operations = [
        migrations.AddField(
            model_name='opportunitydocumentclassificationrun',
            name='unified_evidence',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
