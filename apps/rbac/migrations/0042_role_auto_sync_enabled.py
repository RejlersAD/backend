from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0041_cleanup_all_unwanted_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='auto_sync_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
