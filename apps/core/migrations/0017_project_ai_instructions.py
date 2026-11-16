from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0016_ai_hub_seed'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectAIInstructions',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('instructions', models.TextField(blank=True, default='')),
                ('discipline_context', models.TextField(blank=True, default='')),
                ('engineering_references', models.TextField(blank=True, default='')),
                ('response_format', models.TextField(blank=True, default='')),
                ('terminology', models.TextField(blank=True, default='')),
                ('evidence_expectations', models.TextField(blank=True, default='')),
                ('review_requirements', models.TextField(blank=True, default='')),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ai_instructions', to='core.project')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'core_project_ai_instructions'},
        ),
    ]
