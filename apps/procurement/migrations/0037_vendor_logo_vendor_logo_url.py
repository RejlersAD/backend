from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0036_projectrelationshipresolution'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='logo',
            field=models.ImageField(blank=True, null=True, upload_to='procurement/vendors/logos/'),
        ),
        migrations.AddField(
            model_name='vendor',
            name='logo_url',
            field=models.URLField(blank=True, max_length=1000),
        ),
    ]
