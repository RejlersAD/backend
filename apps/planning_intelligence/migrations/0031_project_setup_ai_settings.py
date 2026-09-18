import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0030_direct_project_planning'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectSetupAISettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(default='openai', editable=False, max_length=20)),
                ('model', models.CharField(default='gpt-4o', max_length=128)),
                ('api_key_encrypted', models.TextField(editable=False)),
                ('last_tested_at', models.DateTimeField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='project_setup_ai_settings', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
