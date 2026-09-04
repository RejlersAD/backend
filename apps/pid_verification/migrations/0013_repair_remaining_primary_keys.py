"""
Continuation of 0012's repair — a full table-by-table check against
production (``information_schema.table_constraints`` for every table
matching 'pidv_%'/'pidv2_%'/'pid_checker_v2_%') found this is NOT isolated
to ``pidv_legend_sheets``: 30 of 31 P&ID-related tables across all three
apps (pid_verification, pid_verification_v2, pid_checker_v2) are missing
their primary key constraint on production, only ``pidv_legend_sheets``
had been manually repaired so far. This migration repairs the remaining
pid_verification tables; see the sibling repair migrations in
pid_verification_v2 and pid_checker_v2 for the rest.

Same idempotent pattern as 0012 — each table is handled independently so
one unexpected table (e.g. real duplicate/null ids) reports and is
skipped rather than aborting the whole migration; safe to run whether or
not some/all of these were already patched manually as an emergency
unblock.
"""
from django.db import migrations

_TABLES = [
    'pidv_projects',
    'pidv_documents',
    'pidv_drawings',
    'pidv_findings',
    'pidv_instrument_symbols',
    'pidv_reference_data',
    'pidv_ai_check_runs',
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
            RAISE NOTICE 'pid_verification 0013 repair FAILED on %: %', tbl, SQLERRM;
        END;
    END LOOP;
END $$;
""".format(tables=', '.join(f"'{t}'" for t in _TABLES))


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0012_repair_legend_sheets_primary_key'),
    ]

    operations = [
        migrations.RunSQL(sql=_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
