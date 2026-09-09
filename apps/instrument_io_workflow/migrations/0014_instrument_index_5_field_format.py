"""
Replaces the 'instrument_index' legend's 4-field definition (from migration
0013: unit-function_code-sequence[-site_symbol]) with the 5-field breakdown
from the project's official legend sheet:

    XX - XX - AAAA - XXXX - X
    area_code - plant_area_code - instrument_classification - sequence_number - suffix(optional)

Only the exact seeded default row is touched (matched by name), same
caution as 0013 — never overwrites a legend a user has since customized.

The instrument_classification lookup table (35 ISA-5.1-style codes,
including the XZSC/XZSO entries added in 0013) is carried over unchanged
under the renamed field key 'instrument_classification'.
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

NEW_NAME = 'Instrument Index — XX-XX-AAAA-XXXX-X (default)'
NEW_DESCRIPTION = (
    'Instrument tag numbering per the project legend sheet: area code, '
    'plant area code, ISA instrument classification code, sequence '
    'number, and optional suffix.'
)

OLD_NAME = 'Instrument Index — UNIT-FUNCTION-SEQUENCE[-SITE] (default)'


def fix_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    updated = IOListLegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(
        name=NEW_NAME,
        description=NEW_DESCRIPTION,
        definition=NEW_DEFINITION,
    )
    print(f'[instrument_io_workflow] Switched to 5-field format on {updated} '
          f'{SECTION!r} legend row(s).')


def fix_backward(apps, schema_editor):
    # Restores 0013's definition (unit-function_code-sequence[-site_symbol]).
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    OLD_DEFINITION = {
        'separator': '-',
        'fields': [
            {
                'key': 'unit',
                'label': 'Unit Number',
                'regex': r'\d{3,4}',
                'notes': 'Unit/plant number e.g. 113',
            },
            {
                'key': 'function_code',
                'label': 'Function Code',
                'regex': '[A-Z]{2,6}',
                'notes': 'Instrument function code e.g. XZSC, PT, FT',
                'lookup': CLASSIFICATION_LOOKUP,
            },
            {
                'key': 'sequence',
                'label': 'Sequence Number',
                'regex': r'\d{3,5}',
                'notes': 'Sequence number e.g. 9502',
            },
            {
                'key': 'site_symbol',
                'label': 'Site Symbol',
                'regex': '[A-Z]{1,4}',
                'optional': True,
                'notes': 'Optional site symbol e.g. TF',
            },
        ],
    }
    OLD_DESCRIPTION = (
        'Instrument tag numbering for this project: unit/plant number, '
        'instrument function code (ISA-5.1), sequence number, and an optional '
        'trailing site symbol (e.g. 113-XZSC-9502, 113-PT-8001, '
        '113-XZSC-9502-TF).'
    )
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(
        name=OLD_NAME,
        description=OLD_DESCRIPTION,
        definition=OLD_DEFINITION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0013_fix_instrument_index_field_order'),
    ]

    operations = [
        migrations.RunPython(fix_forward, fix_backward),
    ]
