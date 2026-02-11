# Generated migration for notification performance optimization
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['recipient', 'is_read', 'status', 'expires_at'],
                name='notif_unread_opt'
            ),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['recipient', 'is_read'],
                name='notif_read_lookup'
            ),
        ),
    ]
