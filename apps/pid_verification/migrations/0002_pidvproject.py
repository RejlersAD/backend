"""
Migration 0002 — Add PIDVProject table and project FK on PIDVDocument
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Create the projects table
        migrations.CreateModel(
            name='PIDVProject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('project_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('project_name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pid_v_projects',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'pidv_projects',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='pidvproject',
            index=models.Index(fields=['project_id'], name='pidv_project_id_idx'),
        ),

        # 2. Add project FK to PIDVDocument (nullable for backward compat)
        migrations.AddField(
            model_name='pidvdocument',
            name='project',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='documents',
                to='pid_verification.pidvproject',
            ),
        ),
    ]
