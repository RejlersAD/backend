from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0044_signed_po_source_url_capacity')]

    operations = [
        migrations.AddField(model_name='receipt', name='operation_key', field=models.UUIDField(blank=True, editable=False, null=True, unique=True)),
        migrations.AddField(model_name='receipt', name='command_fingerprint', field=models.CharField(blank=True, editable=False, max_length=64)),
        migrations.AddField(model_name='receipt', name='workflow_history', field=models.JSONField(blank=True, default=list, editable=False)),
        *[migrations.AlterField(model_name='receipt', name=name, field=models.BooleanField(blank=True, default=None, null=True))
          for name in ('quality_check_passed', 'dimensional_check_passed', 'visual_inspection_passed', 'material_verification_passed')],
    ]
