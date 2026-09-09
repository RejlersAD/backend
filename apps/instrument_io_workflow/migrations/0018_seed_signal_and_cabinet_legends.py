"""
Seeds one active legend each for the two new sections ('signal_types',
'cabinet_locations'), following the exact same pattern as 0010's
'instrument_symbols' seed: single field, regex + lookup table.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration or these sections.

created_by is copied from an existing seeded legend rather than hardcoded,
so this works the same regardless of which user owns the other rows.
"""
from django.db import migrations

SIGNAL_TYPES_LOOKUP = {
    'AI': 'ANALOG INPUT',
    'AO': 'ANALOG OUTPUT',
    'DI': 'DIGITAL INPUT',
    'DO': 'DIGITAL OUTPUT',
    'PI': 'PULSE INPUT',
    'PO': 'PULSE OUTPUT',
    'SI': 'SERIAL INPUT',
    'SO': 'SERIAL OUTPUT',
    'RTD': 'RESISTANCE TEMPERATURE DETECTOR',
    'TC': 'THERMOCOUPLE',
    'FREQ': 'FREQUENCY INPUT',
    'PWM': 'PULSE WIDTH MODULATION',
}

SIGNAL_TYPES_DEFINITION = {
    'separator': '-',
    'fields': [{
        'key': 'signal_type',
        'label': 'Signal Type',
        'notes': 'I/O signal type',
        'regex': '[A-Z]{2,3}',
        'lookup': SIGNAL_TYPES_LOOKUP,
    }],
}

CABINET_LOCATIONS_LOOKUP = {
    'DCS': 'DISTRIBUTED CONTROL SYSTEM',
    'ESD': 'EMERGENCY SHUTDOWN SYSTEM',
    'CCR': 'CENTRAL CONTROL ROOM',
    'LCP': 'LOCAL CONTROL PANEL',
    'MCC': 'MOTOR CONTROL CENTRE',
    'JB': 'JUNCTION BOX',
    'LJB': 'LOCAL JUNCTION BOX',
    'PCCR': 'PROCESS CONTROL COMPUTER ROOM',
    'FGS': 'FIRE AND GAS SYSTEM',
    'HIPPS': 'HIGH INTEGRITY PRESSURE PROTECTION SYSTEM',
}

CABINET_LOCATIONS_DEFINITION = {
    'separator': '-',
    'fields': [{
        'key': 'location',
        'label': 'Cabinet/Panel Location',
        'notes': 'Physical location of instrument',
        'regex': '[A-Z0-9 /-]+',
        'lookup': CABINET_LOCATIONS_LOOKUP,
    }],
}

SEEDS = [
    ('signal_types', 'Signal Types — Signal Type (default)',
     'I/O signal type matched directly against Legend.', SIGNAL_TYPES_DEFINITION),
    ('cabinet_locations', 'Cabinet/Panel Locations — Location (default)',
     'Physical cabinet/panel location matched directly against Legend.', CABINET_LOCATIONS_DEFINITION),
]


def seed_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    template = IOListLegendSheet.objects.exclude(created_by__isnull=True).first()
    if not template:
        return  # no existing legend to copy created_by from — nothing to seed against

    for section, name, description, definition in SEEDS:
        if IOListLegendSheet.objects.filter(section=section).exists():
            continue  # already seeded (e.g. migration re-run) — don't duplicate
        IOListLegendSheet.objects.create(
            created_by_id=template.created_by_id,
            section=section,
            name=name,
            description=description,
            definition=definition,
            is_active=True,
        )
        print(f'[instrument_io_workflow] Seeded 1 legend for section={section!r}.')


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0017_add_signal_and_cabinet_sections'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
