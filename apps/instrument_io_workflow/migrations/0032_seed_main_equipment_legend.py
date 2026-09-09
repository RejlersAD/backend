"""
Seeds one active legend for the new 'main_equipment' section — single
field, identity name->name lookup, same pattern as 'instrument_symbols'
(0010) and 'piping' (identity lookup shape).

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

LOOKUP = {
    'PIG LAUNCHER / RECEIVER': 'PIG LAUNCHER / RECEIVER',
    'BURN PIT NOZZLE': 'BURN PIT NOZZLE',
    'WELLHEAD': 'WELLHEAD',
    'CENTRIFUGAL PUMP (SUBMERGED SUCTION) ELECTRIC MOTOR DRIVEN':
        'CENTRIFUGAL PUMP (SUBMERGED SUCTION) ELECTRIC MOTOR DRIVEN',
    'TEST / PRODUCTION SEPARATOR': 'TEST / PRODUCTION SEPARATOR',
    'RECIPROCATING PUMP (MOTOR DRIVEN)': 'RECIPROCATING PUMP (MOTOR DRIVEN)',
    'VESSEL - BASIC SYMBOL': 'VESSEL - BASIC SYMBOL',
    'COMPRESSOR': 'COMPRESSOR',
    'CENTRIFUGAL PUMP (VERTICAL TYPE) ELECTRIC MOTOR DRIVEN':
        'CENTRIFUGAL PUMP (VERTICAL TYPE) ELECTRIC MOTOR DRIVEN',
    'FLARE STACK': 'FLARE STACK',
    'CENTRIFUGAL PUMP (MOTOR DRIVEN)': 'CENTRIFUGAL PUMP (MOTOR DRIVEN)',
    'METERING PUMP': 'METERING PUMP',
}

DEFINITION = {
    'separator': '-',
    'fields': [{
        'key': 'equipment_type',
        'label': 'Equipment Type',
        'notes': (
            'Main equipment type matched against Legend. Symbol images '
            'to be uploaded manually.'
        ),
        'regex': '[A-Z0-9 /()-]+',
        'lookup': LOOKUP,
    }],
}

SECTION = 'main_equipment'
NAME = 'Main Equipment — Equipment Type (default)'
DESCRIPTION = (
    'Main equipment type matched directly against Legend. Symbol images '
    'to be uploaded manually.'
)


def seed_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    if IOListLegendSheet.objects.filter(section=SECTION).exists():
        return  # already seeded (e.g. migration re-run) — don't duplicate

    template = IOListLegendSheet.objects.exclude(created_by__isnull=True).first()
    if not template:
        return

    IOListLegendSheet.objects.create(
        created_by_id=template.created_by_id,
        section=SECTION,
        name=NAME,
        description=DESCRIPTION,
        definition=DEFINITION,
        is_active=True,
    )
    print(f'[instrument_io_workflow] Seeded 1 legend for section={SECTION!r}.')


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0031_add_main_equipment_section'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
