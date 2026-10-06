import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sales', '0021_document_intelligence_metadata')]

    operations = [
        migrations.AlterField(
            model_name='opportunitydocumentclassificationrun', name='confidence',
            field=models.PositiveSmallIntegerField(
                default=0, validators=[django.core.validators.MaxValueValidator(100)])),
        migrations.AddConstraint(
            model_name='opportunitydocumentclassificationrun',
            constraint=models.CheckConstraint(
                check=models.Q(('confidence__lte', 100)),
                name='sales_doc_classification_confidence_range')),
    ]
