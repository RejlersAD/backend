from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0041_cleanup_all_unwanted_roles'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'ALTER TABLE rbac_roles '
                        'ADD COLUMN IF NOT EXISTS auto_sync_enabled '
                        'boolean NOT NULL DEFAULT true;'
                    ),
                    reverse_sql=(
                        'ALTER TABLE rbac_roles '
                        'DROP COLUMN IF EXISTS auto_sync_enabled;'
                    ),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='role',
                    name='auto_sync_enabled',
                    field=models.BooleanField(default=True),
                ),
            ],
        ),
    ]
