from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('rbac', '0052_replace_broad_business_grants')]

    operations = [
        migrations.CreateModel(
            name='UserPermissionOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('allowed', models.BooleanField()),
                ('permission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_overrides', to='rbac.permission')),
                ('user_profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permission_overrides', to='rbac.userprofile')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('user_profile', 'permission'), name='rbac_unique_user_permission')]},
        ),
    ]
