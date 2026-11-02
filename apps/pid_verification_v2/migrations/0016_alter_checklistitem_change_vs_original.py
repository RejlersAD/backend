from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification_v2', '0015_pidvdocument_approved_by_pidvdocument_reviewed_by_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pidvchecklistitem',
            name='change_vs_original',
            field=models.CharField(blank=True, max_length=60),
        ),
    ]
