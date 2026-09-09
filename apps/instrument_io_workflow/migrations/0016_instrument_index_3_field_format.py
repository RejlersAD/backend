"""
Replaces the 'instrument_index' legend's 5-field definition (from migration
0014) with the confirmed 3-field format:

    NNN-AAAA-XXXXB
    unit_code - function_code - sequence(+optional trailing letter)

Real examples confirmed by the user: 113-X-9501B, 113-PZT-3191,
113-XHSC-9502B.

Only the exact seeded default row is touched (matched by name), same
caution as 0013/0014 — never overwrites a legend a user has since
customized.

The function_code lookup table is carried over unchanged from 0014 — the
user did not ask to add 'X', 'PZT', or 'XHSC' to it this time (unlike
XZSC/XZSO in 0013, whose ISA-5.1 meaning followed an unambiguous existing
pattern), so nothing is guessed here. These 3 example codes will produce a
soft "not a recognised code" finding (not a format-match failure) until
their meanings are confirmed and added.
"""
from django.db import migrations

SECTION = 'instrument_index'

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
            'key': 'unit_code',
            'label': 'Unit Code',
            'regex': r'\d{3,4}',
            'notes': 'Unit number e.g. 113',
        },
        {
            'key': 'function_code',
            'label': 'Function Code',
            'regex': '[A-Z]{1,6}',
            'notes': 'Instrument function e.g. X, PZT, XHSC',
            'lookup': CLASSIFICATION_LOOKUP,
        },
        {
            'key': 'sequence',
            'label': 'Sequence Number',
            'regex': r'\d{3,5}[A-Z]?',
            'notes': 'Sequence + optional suffix e.g. 9501B, 3191',
        },
    ],
}

NEW_NAME = 'Instrument Index — NNN-AAAA-XXXXB (default)'
NEW_DESCRIPTION = (
    'Instrument tag numbering (3-part): unit code, instrument function '
    'code, and sequence number with optional trailing suffix letter '
    '(e.g. 113-X-9501B, 113-PZT-3191, 113-XHSC-9502B).'
)

OLD_NAME = 'Instrument Index — XX-XX-AAAA-XXXX-X (default)'


def fix_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    updated = IOListLegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(
        name=NEW_NAME,
        description=NEW_DESCRIPTION,
        definition=NEW_DEFINITION,
    )
    print(f'[instrument_io_workflow] Switched to 3-field format on {updated} '
          f'{SECTION!r} legend row(s).')


def fix_backward(apps, schema_editor):
    # Restores 0014's 5-field definition.
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    OLD_DEFINITION = {
        'separator': '-',
        'fields': [
            {
                'key': 'area_code',
                'label': 'Area Code',
                'regex': '[A-Z0-9]{2}',
                'notes': 'Area code e.g. 11',
            },
            {
                'key': 'plant_area_code',
                'label': 'Plant Area Code',
                'regex': '[A-Z0-9]{2}',
                'notes': 'Plant area code',
            },
            {
                'key': 'instrument_classification',
                'label': 'Instrument Classification Code',
                'regex': '[A-Z]{2,6}',
                'notes': 'ISA instrument code e.g. XZSC, PT, FT, TT',
                'lookup': CLASSIFICATION_LOOKUP,
            },
            {
                'key': 'sequence_number',
                'label': 'Sequence Number',
                'regex': r'\d{3,5}',
                'notes': 'Sequence number e.g. 9502',
            },
            {
                'key': 'suffix',
                'label': 'Suffix',
                'regex': '[A-Z]{1,2}',
                'optional': True,
                'notes': 'Optional suffix',
            },
        ],
    }
    OLD_DESCRIPTION = (
        'Instrument tag numbering per the project legend sheet: area code, '
        'plant area code, ISA instrument classification code, sequence '
        'number, and optional suffix.'
    )
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(
        name=OLD_NAME,
        description=OLD_DESCRIPTION,
        definition=OLD_DEFINITION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0015_add_piping_abbreviations'),
    ]

    operations = [
        migrations.RunPython(fix_forward, fix_backward),
    ]
