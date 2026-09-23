from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('process_datasheet', '0025_hmbsourceupload')]
    operations = [
        migrations.AlterField(
            model_name='hmbsourceupload', name='upload_kind',
            field=models.CharField(max_length=24, db_index=True, choices=[
                ('master_template', 'Master template'), ('case_file', 'Case file'),
                ('output_template', 'Output template'),
            ]),
        ),
    ]