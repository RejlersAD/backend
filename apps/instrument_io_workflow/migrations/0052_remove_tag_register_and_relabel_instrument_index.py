"""
Consolidates the two near-duplicate flexible instrument-tag tabs into one:
- Deletes 'tag_register' entirely — it had become an exact duplicate of
  'instrument_index' (both 5-field NNN-AAAA-XXXXB[-X] flexible formats,
  see migrations 0047/0050), and 'tag_register' was the one carrying the
  "Instrument Tagging" label.
- Relabels 'instrument_index' from "Instrument Index" to "Instrument
  Tagging" — id unchanged — taking over that label since tag_register no
  longer exists to hold it.

instrument_index's definition/description are untouched (already the
target flexible format from migration 0050); only its `name` prefix
changes to match the new label.

No live code hardcodes 'tag_register' (only historical migrations do) —
IO_LEGEND_SECTION (the constant that wires a section into automated
extraction-time validation) already points at 'instrument_index', not
'tag_register', so deleting tag_register does not affect the automated
tag check at all.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

TAG_REGISTER_SECTION = 'tag_register'

INSTRUMENT_INDEX_SECTION = 'instrument_index'
INSTRUMENT_INDEX_OLD_NAME = 'Instrument Index — [XX-[XX-]]AAAA-XXXXB[-X] (default)'
INSTRUMENT_INDEX_NEW_NAME = 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)'

# Captured so the delete of tag_register is reversible.
TAG_REGISTER_NAME = 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)'
TAG_REGISTER_DESCRIPTION = (
    'Flexible instrument tag numbering, 2 to 5 parts: optional area code, '
    'optional plant area code, required instrument classification code, '
    'required sequence number (+ optional letter), optional trailing '
    'suffix (e.g. XHSC-9502, 113-XHSC-9502, 11-13-XHSC-9502, '
    '11-13-XHSC-9502-A).'
)
CLASSIFICATION_LOOKUP = {
    'FE': 'FLOW ELEMENT', 'FT': 'FLOW TRANSMITTER', 'FV': 'FLOW CONTROL VALVE',
    'LG': 'LEVEL GAUGE', 'LI': 'LEVEL INDICATOR', 'LT': 'LEVEL TRANSMITTER',
    'LV': 'LEVEL CONTROL VALVE', 'LY': 'LEVEL CONVERTER / POSITIONER',
    'PG': 'PRESSURE GAUGE', 'PI': 'PRESSURE INDICATOR', 'PT': 'PRESSURE TRANSMITTER',
    'PY': 'PRESSURE CONVERTER / POSITIONER', 'TG': 'TEMPERATURE GAUGE',
    'TI': 'TEMPERATURE INDICATOR', 'TT': 'TEMPERATURE TRANSMITTER', 'TW': 'THERMOWELL',
    'XV': 'ON/OFF ISOLATION VALVE', 'BDV': 'BLOWDOWN VALVE',
    'BDY': 'BLOWDOWN SOLENOID / RELAY', 'BPG': 'BLOWDOWN PRESSURE GAUGE',
    'FCV': 'FLOW CONTROL VALVE', 'FIT': 'FLOW INDICATING TRANSMITTER',
    'LCV': 'LEVEL CONTROL VALVE', 'PCV': 'PRESSURE CONTROL VALVE',
    'PSV': 'PRESSURE SAFETY VALVE', 'SDV': 'SHUTDOWN VALVE',
    'SDY': 'SHUTDOWN SOLENOID / RELAY', 'BDHS': 'BLOWDOWN HAND SWITCH (LOCAL PUSH BUTTON)',
    'BPSV': 'BLOWDOWN PRESSURE SAFETY VALVE', 'BZSC': 'BLOWDOWN POSITION SWITCH — CLOSED',
    'BZSO': 'BLOWDOWN POSITION SWITCH — OPEN', 'SZSC': 'SHUTDOWN POSITION SWITCH — CLOSED',
    'SZSO': 'SHUTDOWN POSITION SWITCH — OPEN', 'XZSC': 'POSITION SWITCH — CLOSED',
    'XZSO': 'POSITION SWITCH — OPEN',
}
TAG_REGISTER_DEFINITION = {
    'separator': '-',
    'fields': [
        {'key': 'area_code', 'label': 'Area Code', 'regex': '[A-Z0-9]{1,4}',
         'notes': 'Area code e.g. 11, 113', 'optional': True},
        {'key': 'plant_area_code', 'label': 'Plant Area Code', 'regex': '[A-Z0-9]{1,4}',
         'notes': 'Plant area code e.g. 13', 'optional': True},
        {'key': 'instrument_classification', 'label': 'Instrument Classification Code',
         'regex': '[A-Z]{1,6}', 'notes': 'e.g. XHSC, PZT, PT, TT, X', 'lookup': CLASSIFICATION_LOOKUP},
        {'key': 'sequence_number', 'label': 'Sequence Number', 'regex': r'\d{3,5}[A-Z]?',
         'notes': 'e.g. 9502, 9501B, 3191'},
        {'key': 'suffix', 'label': 'Suffix', 'regex': '[A-Z]{1,2}',
         'notes': 'Optional suffix e.g. A, B', 'optional': True},
    ],
}


def apply_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    deleted, _ = IOListLegendSheet.objects.filter(section=TAG_REGISTER_SECTION).delete()
    print(f'[instrument_io_workflow] Deleted {deleted} {TAG_REGISTER_SECTION!r} legend row(s).')

    updated = IOListLegendSheet.objects.filter(
        section=INSTRUMENT_INDEX_SECTION, name=INSTRUMENT_INDEX_OLD_NAME,
    ).update(name=INSTRUMENT_INDEX_NEW_NAME)
    print(f'[instrument_io_workflow] Relabelled {INSTRUMENT_INDEX_SECTION!r} legend name on {updated} row(s).')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    IOListLegendSheet.objects.filter(
        section=INSTRUMENT_INDEX_SECTION, name=INSTRUMENT_INDEX_NEW_NAME,
    ).update(name=INSTRUMENT_INDEX_OLD_NAME)

    if not IOListLegendSheet.objects.filter(section=TAG_REGISTER_SECTION).exists():
        template = IOListLegendSheet.objects.exclude(created_by__isnull=True).first()
        if template:
            IOListLegendSheet.objects.create(
                created_by_id=template.created_by_id,
                section=TAG_REGISTER_SECTION,
                name=TAG_REGISTER_NAME,
                description=TAG_REGISTER_DESCRIPTION,
                definition=TAG_REGISTER_DEFINITION,
                is_active=True,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0051_remove_tag_register_relabel_instrument_index'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
