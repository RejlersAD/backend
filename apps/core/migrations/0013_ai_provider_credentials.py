import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0012_project_task_work_breakdown_assignments'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AIProviderCredential',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider', models.CharField(choices=[('openai', 'OpenAI'), ('anthropic', 'Anthropic'), ('gemini', 'Google Gemini')], db_index=True, max_length=20)),
                ('label', models.CharField(max_length=120)),
                ('encrypted_key', models.TextField(editable=False)),
                ('enabled', models.BooleanField(default=True)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_tested_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('last_test_status', models.CharField(blank=True, editable=False, max_length=20)),
                ('last_test_reason', models.CharField(blank=True, editable=False, max_length=50)),
                ('last_test_model', models.CharField(blank=True, editable=False, max_length=200)),
                ('created_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['provider', 'created_at', 'id']},
        ),
        migrations.CreateModel(
            name='AIProviderConfiguration',
            fields=[
                ('provider', models.CharField(choices=[('openai', 'OpenAI'), ('anthropic', 'Anthropic'), ('gemini', 'Google Gemini')], max_length=20, primary_key=True, serialize=False)),
                ('enabled', models.BooleanField(default=True)),
                ('model', models.CharField(blank=True, max_length=200)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('selected_credential', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.aiprovidercredential')),
                ('updated_by', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
