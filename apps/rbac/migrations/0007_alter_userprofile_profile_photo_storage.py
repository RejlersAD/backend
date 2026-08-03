# Generated manually — switch profile_photo to use dynamic storage backend
# (AvatarStorage on S3 in production, FileSystemStorage in local dev).
# This is a state-only migration: it does not alter the database schema.

import apps.rbac.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0006_subscriptionfeature_subscriptionplan_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='profile_photo',
            field=models.ImageField(
                blank=True,
                help_text='User profile photo — stored in S3 (production) or local media (dev)',
                null=True,
                storage=apps.rbac.models.get_profile_photo_storage,
                upload_to='profile_photos/',
            ),
        ),
    ]
