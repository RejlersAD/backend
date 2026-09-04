"""
Repair migration — production's ``pidv_legend_sheets`` table is missing its
PRIMARY KEY constraint on ``id`` (confirmed via direct query against
production Postgres: ``SELECT conname, contype FROM pg_constraint WHERE
conrelid = 'pidv_legend_sheets'::regclass AND contype IN ('p','u');``
returned 0 rows). Django's migration history believed this table had a
normal auto-created primary key (every other environment's copy of this
table does); production's actually doesn't — almost certainly from however
this specific table was first created there. This went unnoticed until
pid_checker_v2's 0010 migration tried to add a REAL foreign key
(LegendSymbolImage.legend_sheet) pointing at it, which Postgres correctly
refused with "there is no unique constraint matching given keys for
referenced table pidv_legend_sheets" — nothing before this had ever tried
to reference it with a real FK.

Wrapped in an idempotent existence-check (not a plain ADD CONSTRAINT) so
this is safe to run whether or not the primary key was already added
manually as an emergency unblock before this migration was written —
running it twice, or after the manual fix, is a no-op either way.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0011_rename_pidv_ai_check_runs_run_id_idx_pidv_ai_che_run_id_bdcd91_idx_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'pidv_legend_sheets'::regclass AND contype = 'p'
                    ) THEN
                        ALTER TABLE pidv_legend_sheets
                            ADD CONSTRAINT pidv_legend_sheets_pkey PRIMARY KEY (id);
                    END IF;
                END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
