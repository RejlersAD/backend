# Generated migration for adding S3 excel URL fields to CRSRevision

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crs', '0002_crsrevision_crsrevisionchain_crsrevisionactivity_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='crsrevision',
            name='excel_s3_url',
            field=models.URLField(blank=True, help_text='S3 URL for pre-generated Excel export', max_length=1000, null=True),
        ),
        migrations.AddField(
            model_name='crsrevision',
            name='excel_generated_at',
            field=models.DateTimeField(blank=True, help_text='Timestamp when excel was generated and uploaded', null=True),
        ),
    ]
