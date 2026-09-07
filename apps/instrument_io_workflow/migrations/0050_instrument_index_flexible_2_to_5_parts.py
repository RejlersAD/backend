"""
Updates 'instrument_index' to accept flexible 2-to-5-part tag formats —
same shape as 0047's tag_register update: area_code and plant_area_code
become optional leading fields, instrument_classification and
sequence_number stay required, suffix stays an optional trailing field.

Relies on the compile_legend() leading-optional-field fix from 0047 (in
services/legend_comparison.py) — no further code change needed here, that
fix is general, not specific to any one section.

Only the exact seeded default row is touched (matched by name).

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'instrument_index'
OLD_NAME = 'Instrument Index — NNN-AAAA-XXXXB (default)'
NEW_NAME = 'Instrument Index — [XX-[XX-]]AAAA-XXXXB[-X] (default)'
NEW_DESCRIPTION = (
    'Flexible instrument tag numbering, 2 to 5 parts: optional area code, '
    'optional plant area code, required instrument classification code, '
    'required sequence number (+ optional letter), optional trailing '
    'suffix (e.g. XHSC-9502, 113-XHSC-9502, 11-13-XHSC-9502, '
    '11-13-XHSC-9502-A).'
)

CLASSIFICATION_LOOKUP = {
    'FE': 'FLOW ELEMENT',
    'FT': 'FLOW TRANSMITTER',
    'FV': 'FLOW CONTROL VALVE',
    'LG': 'LEVEL GAUGE',
    'LI': 'LEVEL INDICATOR',
    'LT': 'LEVEL TRANSMITTER',
    'LV': 'LEVEL CONTROL VALVE',
    'LY': 'LEVEL CONVERTER / POSITIONER',
    'PG': 'PRESSURE GAUGE',
    'PI': 'PRESSURE INDICATOR',
    'PT': 'PRESSURE TRANSMITTER',
    'PY': 'PRESSURE CONVERTER / POSITIONER',
    'TG': 'TEMPERATURE GAUGE',
    'TI': 'TEMPERATURE INDICATOR',
    'TT': 'TEMPERATURE TRANSMITTER',
    'TW': 'THERMOWELL',
    'XV': 'ON/OFF ISOLATION VALVE',
    'BDV': 'BLOWDOWN VALVE',
    'BDY': 'BLOWDOWN SOLENOID / RELAY',
    'BPG': 'BLOWDOWN PRESSURE GAUGE',
    'FCV': 'FLOW CONTROL VALVE',
    'FIT': 'FLOW INDICATING TRANSMITTER',
    'LCV': 'LEVEL CONTROL VALVE',
    'PCV': 'PRESSURE CONTROL VALVE',
    'PSV': 'PRESSURE SAFETY VALVE',
    'SDV': 'SHUTDOWN VALVE',
    'SDY': 'SHUTDOWN SOLENOID / RELAY',
    'BDHS': 'BLOWDOWN HAND SWITCH (LOCAL PUSH BUTTON)',
    'BPSV': 'BLOWDOWN PRESSURE SAFETY VALVE',
    'BZSC': 'BLOWDOWN POSITION SWITCH — CLOSED',
    'BZSO': 'BLOWDOWN POSITION SWITCH — OPEN',
    'SZSC': 'SHUTDOWN POSITION SWITCH — CLOSED',
    'SZSO': 'SHUTDOWN POSITION SWITCH — OPEN',
    'XZSC': 'POSITION SWITCH — CLOSED',
    'XZSO': 'POSITION SWITCH — OPEN',
}

NEW_DEFINITION = {
    'separator': '-',
    'fields': [
        {
            'key': 'area_code',
            'label': 'Area Code',
            'regex': '[A-Z0-9]{1,4}',
            'notes': 'Area code e.g. 11, 113',
            'optional': True,
        },
        {
            'key': 'plant_area_code',
            'label': 'Plant Area Code',
            'regex': '[A-Z0-9]{1,4}',
            'notes': 'Plant area code e.g. 13',
            'optional': True,
        },
        {
            'key': 'instrument_classification',
            'label': 'Instrument Classification Code',
            'regex': '[A-Z]{1,6}',
            'notes': 'e.g. XHSC, PZT, PT, TT, X',
            'lookup': CLASSIFICATION_LOOKUP,
        },
        {
            'key': 'sequence_number',
            'label': 'Sequence Number',
            'regex': r'\d{3,5}[A-Z]?',
            'notes': 'e.g. 9502, 9501B, 3191',
        },
        {
            'key': 'suffix',
            'label': 'Suffix',
            'regex': '[A-Z]{1,2}',
            'notes': 'Optional suffix e.g. A, B',
            'optional': True,
        },
    ],
}


def update_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    updated = IOListLegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(
        name=NEW_NAME, description=NEW_DESCRIPTION, definition=NEW_DEFINITION,
    )
    print(f'[instrument_io_workflow] Made {SECTION!r} accept flexible 2-5 part tags on {updated} row(s).')


def update_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0049_rename_manual_values_to_manual_valves'),
    ]

    operations = [
        migrations.RunPython(update_forward, update_backward),
    ]
