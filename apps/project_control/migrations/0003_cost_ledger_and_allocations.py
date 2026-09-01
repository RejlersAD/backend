import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


LEGACY_PARENT_ID_INDEXES = (
    ('procurement_budget', 'pc_source_budget_id_repair_uniq'),
    ('project_control_wbsnode', 'pc_wbsnode_id_repair_uniq'),
)


def _postgres_id_type(cursor, table_name):
    cursor.execute(
        '''
        SELECT data_type, udt_name
        FROM information_schema.columns
        WHERE table_schema = ANY (current_schemas(FALSE))
          AND table_name = %s
          AND column_name = 'id'
        ORDER BY array_position(current_schemas(FALSE), table_schema)
        LIMIT 1
        ''',
        [table_name],
    )
    return cursor.fetchone()


def _repair_uuid_ids(cursor, table):
    """Re-key only ambiguous physical rows while preserving the canonical UUID."""
    cursor.execute(f'SELECT id FROM {table} WHERE id IS NOT NULL')
    used_ids = {str(row[0]) for row in cursor.fetchall()}
    cursor.execute(
        f'''
        WITH ranked AS (
            SELECT
                ctid::text AS row_tid,
                id,
                ROW_NUMBER() OVER (PARTITION BY id ORDER BY ctid) AS duplicate_rank
            FROM {table}
        )
        SELECT row_tid
        FROM ranked
        WHERE id IS NULL OR duplicate_rank > 1
        ORDER BY id NULLS FIRST, row_tid
        ''',
    )
    row_tids = [row[0] for row in cursor.fetchall()]
    for row_tid in row_tids:
        new_id = str(uuid.uuid4())
        while new_id in used_ids:
            new_id = str(uuid.uuid4())
        cursor.execute(
            f'UPDATE {table} SET id = %s WHERE ctid = %s::tid',
            [new_id, row_tid],
        )
        used_ids.add(new_id)
    return len(row_tids)


def _repair_integer_ids(cursor, table):
    cursor.execute(
        f'''
        WITH ranked AS (
            SELECT
                ctid,
                id,
                ROW_NUMBER() OVER (PARTITION BY id ORDER BY ctid) AS duplicate_rank,
                MAX(id) OVER () AS max_id
            FROM {table}
        ),
        rekey AS (
            SELECT
                ctid,
                COALESCE(max_id, 0)
                    + ROW_NUMBER() OVER (ORDER BY id NULLS FIRST, ctid) AS new_id
            FROM ranked
            WHERE id IS NULL OR duplicate_rank > 1
        )
        UPDATE {table} AS target
        SET id = rekey.new_id
        FROM rekey
        WHERE target.ctid = rekey.ctid
        ''',
    )
    return cursor.rowcount


def repair_legacy_parent_ids(apps, schema_editor):
    """Restore ownership keys required by the cost-ledger foreign keys."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return

    existing_tables = set(connection.introspection.table_names())
    quote_name = connection.ops.quote_name
    with connection.cursor() as cursor:
        for table_name, index_name in LEGACY_PARENT_ID_INDEXES:
            if table_name not in existing_tables:
                continue

            table = quote_name(table_name)
            cursor.execute(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
            id_type = _postgres_id_type(cursor, table_name)
            if not id_type:
                raise RuntimeError(
                    f'Cannot repair {table_name}: its id column was not found.'
                )

            _data_type, udt_name = id_type
            if udt_name == 'uuid':
                repaired_rows = _repair_uuid_ids(cursor, table)
            elif udt_name in {'int2', 'int4', 'int8'}:
                repaired_rows = _repair_integer_ids(cursor, table)
            else:
                raise RuntimeError(
                    f'Cannot repair {table_name}.id: unsupported PostgreSQL '
                    f'type {udt_name!r}.'
                )
            if repaired_rows:
                print(
                    f'[project_control.0003] Re-keyed {repaired_rows} '
                    f'duplicate/null {table_name} row(s).'
                )

            constraints = connection.introspection.get_constraints(cursor, table_name)
            id_is_unique = any(
                constraint.get('unique') and constraint.get('columns') == ['id']
                for constraint in constraints.values()
            )
            if not id_is_unique:
                schema_editor.execute(
                    f'CREATE UNIQUE INDEX {quote_name(index_name)} '
                    f'ON {table} ({quote_name("id")})'
                )

            cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [table_name, 'id'])
            sequence_name = cursor.fetchone()[0]
            if sequence_name:
                cursor.execute(
                    f'''
                    SELECT setval(
                        %s::regclass,
                        COALESCE(MAX(id), 1),
                        EXISTS (SELECT 1 FROM {table})
                    )
                    FROM {table}
                    ''',
                    [sequence_name],
                )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('procurement', '0036_projectrelationshipresolution'),
        ('project_control', '0002_planningpackage'),
    ]

    operations = [
        migrations.RunPython(
            repair_legacy_parent_ids,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name='BudgetAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('code', models.CharField(max_length=64)),
                ('name', models.CharField(max_length=255)),
                ('category', models.CharField(blank=True, max_length=64)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('currency', models.CharField(default='AED', max_length=8)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('closed', 'Closed')], db_index=True, default='draft', max_length=12)),
                ('notes', models.TextField(blank=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_control_budgets_approved', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='control_budget_allocations', to='core.project')),
                ('source_budget', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='control_allocations', to='procurement.budget')),
                ('wbs_node', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='budget_allocations', to='project_control.wbsnode')),
            ],
            options={'ordering': ['project', 'wbs_node__sort_order', 'code']},
        ),
        migrations.CreateModel(
            name='CostAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('source_type', models.CharField(choices=[('purchase_requisition', 'Purchase Requisition'), ('purchase_order', 'Purchase Order'), ('invoice_allocation', 'Verified Invoice Allocation'), ('manual', 'Manual')], db_index=True, max_length=30)),
                ('source_id', models.CharField(db_index=True, max_length=64)),
                ('source_reference', models.CharField(blank=True, max_length=120)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('currency', models.CharField(default='AED', max_length=8)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('closed', 'Closed')], db_index=True, default='draft', max_length=12)),
                ('notes', models.TextField(blank=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('allocated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_cost_allocations_created', to=settings.AUTH_USER_MODEL)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_cost_allocations_approved', to=settings.AUTH_USER_MODEL)),
                ('budget_allocation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cost_allocations', to='project_control.budgetallocation')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cost_allocations', to='core.project')),
                ('wbs_node', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cost_allocations', to='project_control.wbsnode')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='CostLedgerEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('entry_key', models.CharField(max_length=255, unique=True)),
                ('entry_type', models.CharField(choices=[('budget', 'Budget'), ('commitment', 'Commitment'), ('actual', 'Actual Cost'), ('adjustment', 'Adjustment')], db_index=True, max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('currency', models.CharField(default='AED', max_length=8)),
                ('source_type', models.CharField(blank=True, db_index=True, max_length=30)),
                ('source_id', models.CharField(blank=True, db_index=True, max_length=64)),
                ('source_reference', models.CharField(blank=True, max_length=120)),
                ('entry_date', models.DateField()),
                ('status', models.CharField(choices=[('posted', 'Posted'), ('reversed', 'Reversed')], db_index=True, default='posted', max_length=12)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('budget_allocation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ledger_entries', to='project_control.budgetallocation')),
                ('cost_allocation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ledger_entries', to='project_control.costallocation')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_cost_ledger_entries_created', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cost_ledger_entries', to='core.project')),
                ('wbs_node', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ledger_entries', to='project_control.wbsnode')),
            ],
            options={'ordering': ['-entry_date', '-created_at']},
        ),
        migrations.AddConstraint(model_name='budgetallocation', constraint=models.UniqueConstraint(fields=('project', 'code'), name='pc_budget_project_code_uniq')),
        migrations.AddConstraint(model_name='budgetallocation', constraint=models.CheckConstraint(check=models.Q(amount__gt=0), name='pc_budget_amount_positive')),
        migrations.AddIndex(model_name='budgetallocation', index=models.Index(fields=['project', 'status'], name='pc_budget_proj_status_idx')),
        migrations.AddConstraint(model_name='costallocation', constraint=models.UniqueConstraint(condition=models.Q(is_deleted=False), fields=('source_type', 'source_id', 'project', 'wbs_node'), name='pc_cost_source_wbs_uniq')),
        migrations.AddConstraint(model_name='costallocation', constraint=models.CheckConstraint(check=models.Q(amount__gt=0), name='pc_cost_amount_positive')),
        migrations.AddIndex(model_name='costallocation', index=models.Index(fields=['project', 'status'], name='pc_cost_proj_status_idx')),
        migrations.AddIndex(model_name='costallocation', index=models.Index(fields=['source_type', 'source_id', 'status'], name='pc_cost_source_status_idx')),
        migrations.AddConstraint(model_name='costledgerentry', constraint=models.CheckConstraint(check=models.Q(amount__gte=0), name='pc_ledger_amount_nonnegative')),
        migrations.AddIndex(model_name='costledgerentry', index=models.Index(fields=['project', 'entry_type', 'status'], name='pc_ledger_proj_type_idx')),
        migrations.AddIndex(model_name='costledgerentry', index=models.Index(fields=['project', 'wbs_node', 'status'], name='pc_ledger_proj_wbs_idx')),
    ]
