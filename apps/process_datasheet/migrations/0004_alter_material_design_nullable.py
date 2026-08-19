# Generated migration to fix material_design NOT NULL constraint
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0003_pumpcalculationdata'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE pump_calculation_data ALTER COLUMN material_design DROP NOT NULL;",
            reverse_sql="ALTER TABLE pump_calculation_data ALTER COLUMN material_design SET NOT NULL;",
        ),
    ]
