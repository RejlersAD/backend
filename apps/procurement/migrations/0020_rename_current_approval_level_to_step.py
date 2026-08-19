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
                ALTER TABLE procurement_requisitions 
                RENAME COLUMN current_approval_level TO current_approval_step;
            """,
            reverse_sql="""
                ALTER TABLE procurement_requisitions 
                RENAME COLUMN current_approval_step TO current_approval_level;
            """,
        ),
    ]
