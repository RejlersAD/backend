from django.db import migrations, models
import apps.pid_verification.models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0002_pidvproject'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pidvdocument',
            name='original_file',
            field=models.FileField(
                blank=True,
                max_length=500,
                null=True,
                upload_to=apps.pid_verification.models._pid_upload_path,
            ),
        ),
    ]
