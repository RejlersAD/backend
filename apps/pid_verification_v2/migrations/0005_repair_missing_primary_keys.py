"""
Repair migration — production is missing primary key constraints on every
pid_verification_v2 table (confirmed via a full check against
information_schema.table_constraints: 30 of 31 P&ID-related tables across
pid_verification, pid_verification_v2, and pid_checker_v2 were missing
their primary key on production; see the sibling repair migrations in
those two apps for the rest). Same underlying drift as
pid_verification/migrations/0012 and 0013 — see those docstrings for the
full story.

Idempotent and per-table fault-isolated: safe to run whether or not this
was already patched manually as an emergency unblock, and one unexpected
table doesn't abort the rest.
"""
from django.db import migrations

_TABLES = [
    'pidv2_projects',
    'pidv2_documents',
    'pidv2_drawings',
    'pidv2_findings',
    'pidv2_legend_sheets',
    'pidv2_instrument_symbols',
    'pidv2_reference_data',
    'pidv2_extraction_results',
    'pidv2_extraction_pages',
    'pidv2_comparison_findings',
    'pidv2_comparison_findings_source_extractions',
    'pidv2_ai_check_runs',
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
            RAISE NOTICE 'pid_verification_v2 0005 repair FAILED on %: %', tbl, SQLERRM;
        END;
    END LOOP;
END $$;
""".format(tables=', '.join(f"'{t}'" for t in _TABLES))


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification_v2', '0004_pidvproject_metadata'),
    ]

    operations = [
        migrations.RunSQL(sql=_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
