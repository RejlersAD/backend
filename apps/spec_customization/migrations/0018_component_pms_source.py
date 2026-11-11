from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('spec_customization', '0016_import_pilot')]
    operations = [
        migrations.AddField(model_name='pipingclasscomponent', name='pms_commodity_code', field=models.CharField(blank=True, default='', max_length=128)),
        migrations.AddField(model_name='pipingclasscomponent', name='source_reference', field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='pipingclasscomponent', name='source_read_status', field=models.CharField(blank=True, default='', max_length=16)),
    ]
