from django.db import migrations


NOTIFICATION_MODELS_IN_DEPENDENCY_ORDER = (
    'NotificationCategory',
    'Notification',
    'NotificationPreference',
    'NotificationLog',
)


def repair_missing_notification_tables(apps, schema_editor):
    """Recreate notification tables when migration history drifted from the DB."""
    connection = schema_editor.connection
    existing_tables = set(connection.introspection.table_names())
    created_tables = set()

    for model_name in NOTIFICATION_MODELS_IN_DEPENDENCY_ORDER:
        model = apps.get_model('notifications', model_name)
        table_name = model._meta.db_table
        if table_name not in existing_tables:
            schema_editor.create_model(model)
            existing_tables.add(table_name)
            created_tables.add(table_name)

    # create_model() includes these indexes for newly created tables. The
    # idempotent statements below also repair databases where the main table
    # survived but indexes from migrations 0001/0002 did not.
    if 'notifications' in created_tables:
        return

    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS notificatio_recipie_dde14f_idx '
        'ON notifications (recipient_id, is_read, created_at DESC)'
    )
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS notificatio_priorit_b3d0b6_idx '
        'ON notifications (priority, created_at DESC)'
    )
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS notificatio_status_f96f3f_idx '
        'ON notifications (status, created_at DESC)'
    )
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS notificatio_categor_1f09fa_idx '
        'ON notifications (category_id, created_at DESC)'
    )
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS notif_unread_opt '
        'ON notifications (recipient_id, is_read, status, expires_at)'
    )
    schema_editor.execute(
        'CREATE INDEX IF NOT EXISTS notif_read_lookup '
        'ON notifications (recipient_id, is_read)'
    )


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0002_notification_performance_indexes'),
    ]

    operations = [
        # Database-only repair. Migrations 0001 and 0002 already contain the
        # complete Django state, but some databases recorded them without
        # creating the physical tables.
        migrations.RunPython(
            repair_missing_notification_tables,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
