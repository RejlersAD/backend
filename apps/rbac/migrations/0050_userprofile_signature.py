from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('rbac', '0049_userprofile_employee')]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='signature_image',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='signature_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
