# Generated for the targeted missing-tag locator (locate_tags_on_drawing)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('electrical_comparison', '0006_electricalcomparisonjob_equipment_file_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='electricalcomparisonjob',
            name='located_boxes',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
