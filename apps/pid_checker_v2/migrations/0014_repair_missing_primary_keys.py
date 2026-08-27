"""
Repair migration — production is missing primary key constraints across
pid_checker_v2's own tables too (confirmed via a full check against
information_schema.table_constraints: 30 of 31 P&ID-related tables across
pid_verification, pid_verification_v2, and pid_checker_v2 were missing
their primary key on production). See pid_verification/migrations/0012
and 0013 for the full story and the sibling repair in
pid_verification_v2/migrations/0005.

Idempotent and per-table fault-isolated: safe to run whether or not this
was already patched manually as an emergency unblock, and one unexpected
table doesn't abort the rest.
"""
from django.db import migrations

_TABLES = [
    'pid_checker_v2_project',
    'pid_checker_v2_extraction',
    'pid_checker_v2_line_tag',
    'pid_checker_v2_legend_sheet',
    'pid_checker_v2_line_list_upload',
    'pid_checker_v2_line_list_row',
    'pid_checker_v2_equipment_list_upload',
    'pid_checker_v2_equipment_list_row',
    'pid_checker_v2_instrument_index_upload',
    'pid_checker_v2_instrument_index_row',
    'pid_checker_v2_usage_log',
]

_SQL = """
DO $$
DECLARE
    tbl text;
    tables text[] := ARRAY[{tables}];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = tbl::regclass AND contype = 'p'
            ) THEN
                EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I PRIMARY KEY (id);', tbl, tbl || '_pkey');
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pid_checker_v2 0014 repair FAILED on %: %', tbl, SQLERRM;
        END;
    END LOOP;
END $$;
""".format(tables=', '.join(f"'{t}'" for t in _TABLES))


class Migration(migrations.Migration):

    dependencies = [
        ('pid_checker_v2', '0013_line_tag_confidence'),
        # This repair must run BEFORE 0010's foreign keys are (re-)created on
        # a fresh environment, but since 0010 already applied its
        # CreateModel/AlterField successfully by the time this runs on an
        # existing environment, ordering here only matters for a brand-new
        # database — where Django applies the whole dependency graph
        # together, so pid_verification's own repairs (0012/0013) already
        # being a dependency of 0010 covers that case.
    ]

    operations = [
        migrations.RunSQL(sql=_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
