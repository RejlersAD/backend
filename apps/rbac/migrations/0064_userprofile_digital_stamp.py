from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('rbac', '0063_executive_dashboard_read_access')]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='stamp_image',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='stamp_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
