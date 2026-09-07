"""
Replaces the definitions on 'tag_register' and 'equipment_register' —
until now still literally P&ID's original line-list (6-field
size-service-serial-spec-dep-insulation) and equipment-list
(item_symbol-sequence-site_symbol) formats — with genuinely I/O-List
formats confirmed by the user.

tag_register: same 3-field NNN-AAAA-XXXXB shape as instrument_index
(unit_code-function_code-sequence), reusing instrument_index's
function_code lookup table for consistency — these two tabs now
deliberately overlap in shape; that's what was asked for.

equipment_register: new 6-field XX-XX-AAAA-XX-XX-XX format
(area_code-plant_area_code-equipment_code-system_code-unit_number-
sequence_number). No lookup table was specified for equipment_code, so
none is invented here (the old item_symbol lookup used single-letter
keys that don't even satisfy the new field's 2-6-letter regex, so it
isn't a safe carry-over either).

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

# Same lookup as instrument_index (migration 0016) — kept in sync since
# tag_register now uses the identical 3-field tag shape.
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
    'XZSC': 'POSITION SWITCH — CLOSED',
    'XZSO': 'POSITION SWITCH — OPEN',
}

TAG_REGISTER_DEFINITION = {
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
            'notes': 'e.g. XHSC, PZT, X',
            'lookup': FUNCTION_CODE_LOOKUP,
        },
        {
            'key': 'sequence',
            'label': 'Sequence Number',
            'regex': r'\d{3,5}[A-Z]?',
            'notes': 'e.g. 9502, 9501B',
        },
    ],
}
TAG_REGISTER_NAME = 'Tag Register — NNN-AAAA-XXXXB (default)'
TAG_REGISTER_DESCRIPTION = (
    'Instrument tag numbering (3-part): unit code, instrument function '
    'code, and sequence number with optional trailing suffix letter '
    '(e.g. 113-X-9501B, 113-PZT-3191, 113-XHSC-9502B).'
)

EQUIPMENT_REGISTER_DEFINITION = {
    'separator': '-',
    'fields': [
        {
            'key': 'area_code',
            'label': 'Area Code',
            'regex': r'\d{2,3}',
        },
        {
            'key': 'plant_area_code',
            'label': 'Plant Area Code',
            'regex': '[A-Z0-9]{2}',
        },
        {
            'key': 'equipment_code',
            'label': 'Equipment Code',
            'regex': '[A-Z]{2,6}',
        },
        {
            'key': 'system_code',
            'label': 'System Code',
            'regex': '[A-Z0-9]{2}',
        },
        {
            'key': 'unit_number',
            'label': 'Unit Number',
            'regex': '[A-Z0-9]{2}',
        },
        {
            'key': 'sequence_number',
            'label': 'Sequence Number',
            'regex': r'\d{2,4}',
        },
    ],
}
EQUIPMENT_REGISTER_NAME = 'Equipment Register — XX-XX-AAAA-XX-XX-XX (default)'
EQUIPMENT_REGISTER_DESCRIPTION = (
    'Equipment numbering: area code, plant area code, equipment code, '
    'system code, unit number and sequence number.'
)

# section -> (old exact name to match, new name, new description, new definition)
UPDATES = {
    'tag_register': (
        'Tag Register — XX-XX-XXXX-XXXX-X (default)',
        TAG_REGISTER_NAME, TAG_REGISTER_DESCRIPTION, TAG_REGISTER_DEFINITION,
    ),
    'equipment_register': (
        'Equipment Register — XX-XXX-XX (default)',
        EQUIPMENT_REGISTER_NAME, EQUIPMENT_REGISTER_DESCRIPTION, EQUIPMENT_REGISTER_DEFINITION,
    ),
}


def update_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    for section, (old_name, new_name, new_desc, new_def) in UPDATES.items():
        updated = IOListLegendSheet.objects.filter(section=section, name=old_name).update(
            name=new_name, description=new_desc, definition=new_def,
        )
        print(f'[instrument_io_workflow] Updated {section!r} to I/O List format on {updated} row(s).')


def update_backward(apps, schema_editor):
    # Not restoring the old P&ID-derived definitions on reverse — those
    # were exactly what this migration was asked to remove. Reverse just
    # restores the display name so the migration is technically invertible
    # without resurrecting the old field shapes.
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    for section, (old_name, new_name, _new_desc, _new_def) in UPDATES.items():
        IOListLegendSheet.objects.filter(section=section, name=new_name).update(name=old_name)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0021_rename_legend_names_to_match_sections'),
    ]

    operations = [
        migrations.RunPython(update_forward, update_backward),
    ]
