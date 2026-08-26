"""
Fix: Rename current_approval_level to current_approval_step in procurement_requisitions table
This resolves the ProgrammingError when fetching purchase orders
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0019_procurement_number_sequence'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'procurement_requisitions'
                          AND column_name = 'current_approval_level'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'procurement_requisitions'
                          AND column_name = 'current_approval_step'
                    ) THEN
                        ALTER TABLE procurement_requisitions
                        RENAME COLUMN current_approval_level TO current_approval_step;
                    END IF;
                END $$;
            """,
            reverse_sql="""
                DO $$ BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'procurement_requisitions'
                          AND column_name = 'current_approval_step'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'procurement_requisitions'
                          AND column_name = 'current_approval_level'
                    ) THEN
                        ALTER TABLE procurement_requisitions
                        RENAME COLUMN current_approval_step TO current_approval_level;
                    END IF;
                END $$;
            """,
        ),
    ]
