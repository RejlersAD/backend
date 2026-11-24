from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0039_quote_draft_optional_estimates')]

    operations = [
        migrations.AddField(
            model_name='opportunitydocumentclassificationrun', name='tag_details',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='opportunitydocumentclassificationrun', name='content_profile',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
