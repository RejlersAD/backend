from django.db import migrations, models
import django.db.models.deletion


def ensure_override_reference_keys(apps, schema_editor):
    """Repair missing PostgreSQL reference keys without changing identities.

    A restored database can record 0001 as applied while missing its indexes.
    This preflight must run here, before CreateModel's deferred foreign keys.
    A later migration alone cannot unblock a deployment stopped at 0053.
    """
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    quote = schema_editor.quote_name
    with connection.cursor() as cursor:
        for name in ('Permission', 'UserProfile'):
            model = apps.get_model('rbac', name)
            table, column = model._meta.db_table, model._meta.pk.column
            # Hold the lock through validation and constraint creation, avoiding
            # concurrent inserts between the duplicate check and ALTER TABLE.
            cursor.execute(f'LOCK TABLE {quote(table)} IN ACCESS EXCLUSIVE MODE')
            cursor.execute('''
                SELECT EXISTS (
                    SELECT 1 FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid
                    WHERE i.indrelid = to_regclass(%s) AND a.attname = %s
                      AND i.indisunique AND i.indisvalid AND i.indisready
                      AND i.indimmediate AND i.indpred IS NULL
                      AND i.indexprs IS NULL AND i.indnkeyatts = 1
                      AND i.indkey[0] = a.attnum
                )
            ''', [table, column])
            if cursor.fetchone()[0]:
                continue
            cursor.execute(f'SELECT EXISTS (SELECT 1 FROM {quote(table)} WHERE {quote(column)} IS NULL)')
            if cursor.fetchone()[0]:
                raise RuntimeError(f'Cannot repair {table}.{column}: null IDs exist. Reconcile identities before retrying; no permission data was changed.')
            cursor.execute(f'''SELECT EXISTS (
                SELECT 1 FROM {quote(table)} GROUP BY {quote(column)} HAVING COUNT(*) > 1
            )''')
            if cursor.fetchone()[0]:
                raise RuntimeError(f'Cannot repair {table}.{column}: duplicate IDs exist. Reconcile identities before retrying; no permission data was changed.')
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(%s) AND contype = 'p')", [table])
            kind = 'UNIQUE' if cursor.fetchone()[0] else 'PRIMARY KEY'
            schema_editor.execute(f'ALTER TABLE {quote(table)} ADD {kind} ({quote(column)})')


class Migration(migrations.Migration):
    dependencies = [('rbac', '0052_replace_broad_business_grants')]

    operations = [
        migrations.RunPython(ensure_override_reference_keys, migrations.RunPython.noop),
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
