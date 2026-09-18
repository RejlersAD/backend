from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('procurement', '0043_project_relationship_exceptions')]

    operations = [
        migrations.AlterField(
            model_name='purchaseorder',
            name='approval_stamp',
            field=models.TextField(blank=True, help_text='Company stamp image (S3 URL)'),
        ),
        migrations.AlterField(
            model_name='podocument',
            name='s3_url',
            field=models.TextField(blank=True),
        ),
    ]
