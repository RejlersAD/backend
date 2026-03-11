# Generated manually to resolve migration conflict
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('process_datasheet', '0004_alter_material_design_nullable'),
        ('process_datasheet', '0004_discharge_pressure_calculations'),
    ]

    # This is a merge migration with no operations
    # It resolves the conflict between the two 0004 migrations
    # Will be fake-applied in production since no database changes are needed
    operations = [
    ]
