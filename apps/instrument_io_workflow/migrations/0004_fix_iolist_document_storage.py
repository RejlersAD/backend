from django.db import migrations, models

import apps.instrument_io_workflow.models


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0003_iolistproject_iolistdocument_project_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='iolistdocument',
            name='pdf_file',
            field=models.FileField(
                storage=apps.instrument_io_workflow.models.get_io_list_document_storage,
                upload_to='instrument_io_workflow/%Y/%m/',
            ),
        ),
    ]
