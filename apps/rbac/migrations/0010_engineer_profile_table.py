"""
Migration: create rbac_engineer_profiles table
Generated manually — 2026-04-08
"""
import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0009_full_rbac_upgrade'),
    ]

    operations = [
        migrations.CreateModel(
            name='EngineerProfile',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('expertise_level', models.CharField(
                    blank=True,
                    choices=[
                        ('junior',    'Junior'),
                        ('mid',       'Mid-Level'),
                        ('senior',    'Senior'),
                        ('principal', 'Principal'),
                        ('lead',      'Lead'),
                        ('manager',   'Engineering Manager'),
                    ],
                    max_length=20,
                )),
                ('years_experience', models.PositiveIntegerField(default=0)),
                ('engineering_disciplines', models.JSONField(blank=True, default=list)),
                ('technical_skills',        models.JSONField(blank=True, default=list)),
                ('languages',               models.JSONField(blank=True, default=list)),
                ('certifications',          models.JSONField(blank=True, default=list)),
                ('availability_status', models.CharField(
                    choices=[
                        ('available',  'Available'),
                        ('partial',    'Partially Available'),
                        ('busy',       'Fully Committed'),
                        ('on_leave',   'On Leave'),
                    ],
                    default='available',
                    max_length=20,
                )),
                ('availability_percentage', models.PositiveIntegerField(default=100)),
                ('next_available_date',     models.DateField(blank=True, null=True)),
                ('max_concurrent_projects', models.PositiveIntegerField(default=2)),
                ('preferred_project_types', models.JSONField(blank=True, default=list)),
                ('current_projects',        models.JSONField(blank=True, default=list)),
                ('user_profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='engineer_profile',
                    to='rbac.userprofile',
                )),
            ],
            options={
                'verbose_name': 'Engineer Profile',
                'verbose_name_plural': 'Engineer Profiles',
                'db_table': 'rbac_engineer_profiles',
                'indexes': [
                    models.Index(fields=['expertise_level'],    name='rbac_ep_expertise_idx'),
                    models.Index(fields=['availability_status'], name='rbac_ep_avail_idx'),
                ],
            },
        ),
    ]
