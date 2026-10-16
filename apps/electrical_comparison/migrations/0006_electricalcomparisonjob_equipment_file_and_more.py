# Generated for the document canvas viewer (AnnotatedPagesView)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('electrical_comparison', '0005_electricalcomparisonjob_pid_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='electricalcomparisonjob',
            name='equipment_file',
            field=models.FileField(blank=True, null=True, upload_to='electrical_comparison/equipment_lists/%Y/%m/'),
        ),
        migrations.AddField(
            model_name='electricalcomparisonjob',
            name='load_list_file',
            field=models.FileField(blank=True, null=True, upload_to='electrical_comparison/load_lists/%Y/%m/'),
        ),
    ]
