import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Project',
            fields=[
                ('project_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('code', models.CharField(blank=True, db_index=True, max_length=64)),
                ('client', models.CharField(blank=True, max_length=128)),
                ('plant', models.CharField(blank=True, max_length=128)),
                ('discipline', models.CharField(blank=True, max_length=64)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('on_hold', 'On hold'), ('completed', 'Completed'), ('archived', 'Archived')], db_index=True, default='active', max_length=20)),
                ('tags', models.JSONField(blank=True, default=list)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='organizer_projects', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Project',
                'verbose_name_plural': 'Projects',
                'ordering': ['-updated_at', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ProjectActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tool_code', models.CharField(db_index=True, max_length=64)),
                ('summary', models.CharField(max_length=255)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='organizer_project_activity', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activity', to='project_organizer.project')),
            ],
            options={
                'verbose_name': 'Project Activity',
                'verbose_name_plural': 'Project Activity',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='project',
            index=models.Index(fields=['status', '-updated_at'], name='project_org_status_98d3a1_idx'),
        ),
        migrations.AddIndex(
            model_name='project',
            index=models.Index(fields=['created_by', '-updated_at'], name='project_org_created_5e1c2b_idx'),
        ),
        migrations.AddIndex(
            model_name='projectactivity',
            index=models.Index(fields=['project', '-created_at'], name='project_org_project_7a4f6d_idx'),
        ),
        migrations.AddIndex(
            model_name='projectactivity',
            index=models.Index(fields=['tool_code', '-created_at'], name='project_org_toolcod_c9b8e2_idx'),
        ),
    ]
