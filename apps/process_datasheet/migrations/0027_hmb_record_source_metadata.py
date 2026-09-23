from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('process_datasheet', '0026_hmb_output_template_kind')]
    operations = [migrations.AddField(
        model_name='hmbcaserecord', name='source_metadata', field=models.JSONField(default=dict, blank=True),
    )]