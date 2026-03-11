# Generated merge migration to resolve concurrent branches
# This merges:
# - 0004_alter_material_design_nullable
# - 0004_discharge_pressure_calculations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0004_alter_material_design_nullable'),
        ('process_datasheet', '0004_discharge_pressure_calculations'),
    ]

    operations = [
        # No operations needed - this is just a merge point
    ]
