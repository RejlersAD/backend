"""
Corrects the 'instrument_index' legend's field order to match this
project's real tag format.

Real tags (confirmed against live extracted document data) are
UNIT-FUNCTION_CODE-SEQUENCE[-SITE_SYMBOL], e.g.:
    113-XZSC-9502       (unit 113, function XZSC, sequence 9502)
    113-PT-8001
    113-XZSC-9502-TF    (with optional trailing site symbol)

The previously seeded definition was FUNCTION_CODE-LOOP_NUMBER-SITE_SYMBOL
(letters-first) — a different, ISA-5.1-style convention that does not match
how this project's tags are actually built, which caused every real
extracted tag to be flagged as "does not match format".

This migration only replaces the `definition` JSON on the existing active
'instrument_index' row (name/description updated to match); no schema
change. The function_code lookup table (33 ISA-5.1 codes) is carried over
unchanged from the previous definition — nothing invented, nothing dropped.

apps.instrument_io_workflow.services.legend_comparison.compile_legend()
builds its regex purely from whatever `fields` list a definition provides
(no hardcoded field names/positions/count), so this is a pure data fix —
no code changes are needed there for the new field order or the new
optional trailing field to work correctly.
"""
from django.db import migrations

SECTION = 'instrument_index'

FUNCTION_CODE_LOOKUP = {
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
    # 'X' = ISA-5.1 unclassified first-letter (no specific system prefix),
    # same 'Z' (position deviation) + 'S' (switch) + 'C'/'O' (closed/open)
    # combination as BZSC/BZSO/SZSC/SZSO above. Confirmed present in this
    # project's real tag data (e.g. 113-XZSC-9502).
    'XZSC': 'POSITION SWITCH — CLOSED',
    'XZSO': 'POSITION SWITCH — OPEN',
}

NEW_DEFINITION = {
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
            'lookup': FUNCTION_CODE_LOOKUP,
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
            'notes': 'Optional site symbol e.g. TF',
            'optional': True,
        },
    ],
}

NEW_NAME = 'Instrument Index — UNIT-FUNCTION-SEQUENCE[-SITE] (default)'
NEW_DESCRIPTION = (
    'Instrument tag numbering for this project: unit/plant number, '
    'instrument function code (ISA-5.1), sequence number, and an optional '
    'trailing site symbol (e.g. 113-XZSC-9502, 113-PT-8001, '
    '113-XZSC-9502-TF).'
)

OLD_DEFINITION = {
    'separator': '-',
    'fields': [
        {
            'key': 'function_code',
            'label': 'Function Code',
            'notes': 'ISA-5.1 measured-variable + modifier + readout letters (1–4 letters)',
            'regex': '[A-Z]{1,4}',
            'lookup': FUNCTION_CODE_LOOKUP,
        },
        {
            'key': 'loop_number',
            'label': 'Loop / Sequence Number',
            'notes': (
                'Loop number (3–4 digits) with optional trailing letter suffix '
                'for parallel or A/B trains (e.g. 8003A, 8004B).'
            ),
            'regex': r'\d{3,4}[A-Z]?',
        },
        {
            'key': 'site_symbol',
            'label': 'Site Symbol',
            'notes': (
                "Site / platform / jacket identifier (2 letters). May appear "
                "space-separated ('LT-8019 TF') or joined ('PT-8003ATF')."
            ),
            'regex': '[A-Z]{2}',
            'lookup': {
                'AA': 'PRODUCTION PLATFORM / JACKET (AA)',
                'BA': 'PRODUCTION PLATFORM / JACKET (BA)',
                'BB': 'PRODUCTION PLATFORM / JACKET (BB)',
                'BC': 'PRODUCTION PLATFORM / JACKET (BC)',
                'BD': 'PRODUCTION PLATFORM / JACKET (BD)',
                'BF': 'PRODUCTION PLATFORM / JACKET (BF)',
                'CA': 'PRODUCTION PLATFORM / JACKET (CA)',
                'CD': 'PRODUCTION PLATFORM / JACKET (CD)',
                'CF': 'CFP',
                'HF': 'HAIL FIELD / HAIL SITE TERMINAL',
                'TF': 'MUBARRAZ ISLAND',
            },
            'optional': True,
        },
    ],
}
OLD_NAME = 'Instrument Index — XX-NNNN[A] SS (default)'
OLD_DESCRIPTION = (
    'Instrument tag numbering (ISA-5.1 style): function code, loop/sequence '
    'number with optional letter suffix, and site symbol (e.g. LT-8019 TF, '
    'PT-8003A TF, PCV-8004B TF).'
)


def fix_forward(apps, schema_editor):
    # Only touch the exact seeded default row (matched by name), same
    # caution as migration 0012's rename — never overwrite a legend a user
    # has since customized/renamed themselves.
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    updated = IOListLegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(
        name=NEW_NAME,
        description=NEW_DESCRIPTION,
        definition=NEW_DEFINITION,
    )
    print(f'[instrument_io_workflow] Fixed field order on {updated} '
          f'{SECTION!r} legend row(s).')


def fix_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(
        name=OLD_NAME,
        description=OLD_DESCRIPTION,
        definition=OLD_DEFINITION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0012_rename_instrument_symbols_to_instruments'),
    ]

    operations = [
        migrations.RunPython(fix_forward, fix_backward),
    ]
