from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0020_aiworkspace_aiworkspaceconversation_and_more')]

    operations = [
        migrations.AlterField(
            model_name='aiworkspaceconversation',
            name='workspace',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='conversations', to='core.aiworkspace'),
        ),
    ]
