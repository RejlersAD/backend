from django.db import migrations


REPAIR_DAILY_ATTENDANCE_SUMMARY_SQL = r"""
ALTER TABLE timesheet_dailyattendancesummary
    ADD COLUMN IF NOT EXISTS source varchar(16),
    ADD COLUMN IF NOT EXISTS employee_name varchar(255),
    ADD COLUMN IF NOT EXISTS department varchar(255),
    ADD COLUMN IF NOT EXISTS attendance_status varchar(32),
    ADD COLUMN IF NOT EXISTS time_in time without time zone,
    ADD COLUMN IF NOT EXISTS time_out time without time zone,
    ADD COLUMN IF NOT EXISTS overtime_hours double precision;

UPDATE timesheet_dailyattendancesummary
SET source = 'biometric'
WHERE source IS NULL OR source = '';

UPDATE timesheet_dailyattendancesummary
SET employee_name = ''
WHERE employee_name IS NULL;

UPDATE timesheet_dailyattendancesummary
SET department = ''
WHERE department IS NULL;

UPDATE timesheet_dailyattendancesummary
SET attendance_status = 'present'
WHERE attendance_status IS NULL OR attendance_status = '';

UPDATE timesheet_dailyattendancesummary
SET overtime_hours = 0.0
WHERE overtime_hours IS NULL;

ALTER TABLE timesheet_dailyattendancesummary
    ALTER COLUMN source SET NOT NULL,
    ALTER COLUMN employee_name SET NOT NULL,
    ALTER COLUMN department SET NOT NULL,
    ALTER COLUMN attendance_status SET NOT NULL,
    ALTER COLUMN overtime_hours SET NOT NULL;

CREATE INDEX IF NOT EXISTS ts_daily_summary_source_idx
    ON timesheet_dailyattendancesummary (source);

DO $repair$
DECLARE
    obsolete_constraint text;
BEGIN
    -- Migration 0006 replaced the old employee/date uniqueness rule. Some
    -- restored databases retained that physical constraint while recording
    -- 0006 as applied, which prevents biometric and manual rows coexisting.
    FOR obsolete_constraint IN
        SELECT constraint_columns.conname
        FROM (
            SELECT con.conname,
                   array_agg(att.attname ORDER BY key_column.ordinality) AS columns
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace namespace ON namespace.oid = rel.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey)
                WITH ORDINALITY AS key_column(attnum, ordinality)
            JOIN pg_attribute att
              ON att.attrelid = rel.oid
             AND att.attnum = key_column.attnum
            WHERE namespace.nspname = current_schema()
              AND rel.relname = 'timesheet_dailyattendancesummary'
              AND con.contype = 'u'
            GROUP BY con.conname
        ) AS constraint_columns
        WHERE constraint_columns.columns = ARRAY['employee_code', 'date']::name[]
    LOOP
        EXECUTE format(
            'ALTER TABLE timesheet_dailyattendancesummary DROP CONSTRAINT %I',
            obsolete_constraint
        );
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
        FROM (
            SELECT array_agg(att.attname ORDER BY key_column.ordinality) AS columns
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace namespace ON namespace.oid = rel.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey)
                WITH ORDINALITY AS key_column(attnum, ordinality)
            JOIN pg_attribute att
              ON att.attrelid = rel.oid
             AND att.attnum = key_column.attnum
            WHERE namespace.nspname = current_schema()
              AND rel.relname = 'timesheet_dailyattendancesummary'
              AND con.contype = 'u'
            GROUP BY con.conname
        ) AS constraint_columns
        WHERE constraint_columns.columns =
              ARRAY['employee_code', 'date', 'source']::name[]
    ) THEN
        ALTER TABLE timesheet_dailyattendancesummary
            ADD CONSTRAINT ts_daily_summary_employee_date_source_uniq
            UNIQUE (employee_code, date, source);
    END IF;
END;
$repair$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ('timesheet', '0007_dailyattendancesummary_manual_metadata'),
    ]

    operations = [
        # Database-only repair. Django's state already contains these fields
        # through migrations 0006 and 0007.
        migrations.RunSQL(
            sql=REPAIR_DAILY_ATTENDANCE_SUMMARY_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
