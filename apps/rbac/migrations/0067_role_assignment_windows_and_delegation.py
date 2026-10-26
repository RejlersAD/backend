"""Supply schema already required by the effective-role queries.

Existing assignments retain NULL bounds; no grants/delegations are created.
"""
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0066_piping_pms_full_module_actions'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='userrole', name='effective_from',
            field=models.DateTimeField(blank=True, null=True, help_text='Role assignment becomes active at this time (null = immediately)'),
        ),
        migrations.AddField(
            model_name='userrole', name='expires_at',
            field=models.DateTimeField(blank=True, null=True, help_text='Role assignment expires at this time (null = never expires)'),
        ),
        migrations.AddIndex(
            model_name='userrole',
            index=models.Index(fields=['effective_from', 'expires_at'], name='rbac_user_r_effecti_dc7ef8_idx'),
        ),
        migrations.CreateModel(
            name='Delegation',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('effective_from', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('delegated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('delegatee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegations_received', to='rbac.userprofile')),
                ('delegator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegations_given', to='rbac.userprofile')),
                ('role', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='rbac.role')),
            ],
            options={
                'db_table': 'rbac_delegations',
                'indexes': [
                    models.Index(fields=['delegatee', 'role'], name='rbac_delega_delegat_280264_idx'),
                    models.Index(fields=['delegator', 'role'], name='rbac_delega_delegat_56df7f_idx'),
                    models.Index(fields=['effective_from', 'expires_at'], name='rbac_delega_effecti_cd30ce_idx'),
                    models.Index(fields=['is_active'], name='rbac_delega_is_acti_a63fc5_idx'),
                ],
                'constraints': [models.UniqueConstraint(fields=('delegator', 'delegatee', 'role'), name='rbac_unique_delegation')],
            },
        ),
    ]
