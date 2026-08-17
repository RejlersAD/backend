from django.db import migrations, models


def classify_existing_checklists(apps, schema_editor):
    Checklist = apps.get_model('onboarding', 'Checklist')
    Checklist.objects.filter(
        onboarding_record__isnull=False,
        description='IT onboarding checklist task',
    ).update(stage='it_provisioning')
    Checklist.objects.filter(
        onboarding_record__isnull=False,
        stage='general',
    ).update(stage='final_validation')


class Migration(migrations.Migration):
    dependencies = [('onboarding', '0004_alter_document_verified_by')]

    operations = [
        migrations.AddField(
            model_name='checklist',
            name='stage',
            field=models.CharField(
                choices=[
                    ('general', 'General'),
                    ('pre_hire', 'Pre-Hire Initiation'),
                    ('it_provisioning', 'IT Provisioning'),
                    ('first_day', 'First Day Orientation'),
                    ('final_validation', 'Final Checklist Validation'),
                ],
                db_index=True,
                default='general',
                max_length=30,
            ),
        ),
        migrations.RunPython(classify_existing_checklists, migrations.RunPython.noop),
    ]
