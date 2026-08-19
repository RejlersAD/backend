# Generated manually for adding AI quality checker fields
from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('electrical_datasheet', '0001_initial'),  # Adjust this to match your last migration
    ]

    operations = [
        migrations.AddField(
            model_name='electricaldatasheet',
            name='compliance_score',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='AI-calculated quality/compliance score (0-100)',
                max_digits=5,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100)
                ]
            ),
        ),
        migrations.AddField(
            model_name='electricaldatasheet',
            name='last_quality_check',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
