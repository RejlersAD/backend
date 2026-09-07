from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0037_vendor_logo_vendor_logo_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='business_type',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='vendor',
            name='specialization',
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name='vendor',
            name='website',
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name='vendor',
            name='city',
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
