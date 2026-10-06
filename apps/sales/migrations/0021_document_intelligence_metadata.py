from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0020_document_custom_tag')]

    operations = [
        migrations.AddField(model_name='opportunitydocumentclassificationrun', name='tags',
                            field=models.JSONField(default=list)),
        migrations.AddField(model_name='opportunitydocumentclassificationrun', name='confidence',
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name='opportunitydocumentclassificationrun', name='recommended_folder',
                            field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name='opportunitydocumentclassificationrun', name='reasoning',
                            field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name='opportunitydocumentclassificationrun', name='search_keywords',
                            field=models.JSONField(default=list)),
        migrations.AlterField(model_name='opportunitydocumentclassificationrun', name='engine_version',
                              field=models.CharField(default='document_classification_v2', max_length=50)),
    ]
