"""Allow current Receipt inserts against databases with legacy workflow columns."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0027_repair_legacy_approval_step'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'procurement_receipts'
                          AND column_name = 'cancellation_reason'
                    ) THEN
                        ALTER TABLE procurement_receipts
                        ALTER COLUMN cancellation_reason SET DEFAULT '';
                    END IF;
                END $$;
            """,
            reverse_sql="""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'procurement_receipts'
                          AND column_name = 'cancellation_reason'
                    ) THEN
                        ALTER TABLE procurement_receipts
                        ALTER COLUMN cancellation_reason DROP DEFAULT;
                    END IF;
                END $$;
            """,
        ),
    ]
