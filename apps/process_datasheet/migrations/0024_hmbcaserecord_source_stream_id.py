from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0023_hmb_case_storage'),
    ]

    operations = [
        migrations.AddField(
            model_name='hmbcaserecord',
            name='source_stream_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
    ]