# Generated migration for DOCX letter output files
from django.db import migrations, models

import apps.sales.letter_models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0026_sales_letter_pdf'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesletter',
            name='docx_file',
            field=models.FileField(blank=True, null=True, upload_to=apps.sales.letter_models.letter_pdf_upload_path),
        ),
        migrations.AddField(
            model_name='salesletter',
            name='docx_generated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
