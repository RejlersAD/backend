"""Make the stale approval-step column safe after branch migration drift."""

from django.db import migrations


FORWARD_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'procurement_requisitions'
          AND column_name = 'current_approval_step'
    ) THEN
        EXECUTE '
            UPDATE procurement_requisitions
               SET current_approval_level = COALESCE(current_approval_step, 0)
             WHERE current_approval_level = 0
               AND current_approval_step IS NOT NULL
        ';
        EXECUTE '
            ALTER TABLE procurement_requisitions
            ALTER COLUMN current_approval_step SET DEFAULT 0
        ';
        EXECUTE '
            ALTER TABLE procurement_requisitions
            ALTER COLUMN current_approval_step DROP NOT NULL
        ';
    END IF;
END
$$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0026_repair_purchase_requisition_columns'),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, migrations.RunSQL.noop),
    ]
