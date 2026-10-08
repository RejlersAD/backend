# Generated migration for sales letter PDF fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0025_seed_letter_templates'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesletter',
            name='pdf_file',
            field=models.FileField(blank=True, null=True, upload_to='sales/letters/%Y/%m/'),
        ),
        migrations.AddField(
            model_name='salesletter',
            name='pdf_generated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]