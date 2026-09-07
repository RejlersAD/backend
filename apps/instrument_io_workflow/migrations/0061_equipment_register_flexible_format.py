"""
Replaces 'equipment_register' ("Equipment Numbering" tab)'s field
structure — found while auditing all 19 I/O List legend sections for
overly strict patterns.

The OLD structure demanded 6 MANDATORY dash-separated segments (area_code
-plant_area_code-equipment_code-system_code-unit_number-sequence_number,
e.g. "10-AB-PUMP-CD-EF-001") with equipment_code requiring a 2-6 letter
minimum. Tested directly: this pattern rejected EVERY real equipment tag
checked, including the exact tags already sitting in a real extracted
P&ID document — 'V-101', 'P-203A', 'TK-201', 'E-104B' (plain form) AND
'1520-D-103', '1520-EA-102' (unit-prefixed form). Two separate problems:
  1. equipment_code's 2-letter minimum rejects genuine 1-letter codes
     (V=vessel, D=drum, T=tower, K=compressor/turbine, P=pump, C=
     compressor, E=exchanger — see pid_vision_extractor.py's own
     EQUIPMENT_TAG_PATTERN comment for this exact letter set).
  2. The 6-field shape doesn't match the real convention at all — actual
     equipment tags are 2-3 segments (EQUIPCODE-NUMBER[SUFFIX], or
     UNIT-EQUIPCODE-NUMBER[SUFFIX] when unit-prefixed), and critically
     the unit prefix comes FIRST on a real tag, not last as the old
     'unit_number' field (positioned after equipment_code) would require
     — no reordering of the old 6 fields could have produced a working
     match; this needed a genuine restructure.

New structure mirrors pid_vision_extractor.py's own EQUIPMENT_TAG_PATTERN
exactly: '^(?:\\d{2,4}-)?[A-Z]{1,4}-?\\d{1,5}(?:-?[A-Z]{1,2})?$'
  - unit_number: 2-4 digits, OPTIONAL, leading
  - equipment_code: 1-4 letters, required
  - sequence_number: 1-5 digits + 0-2 optional trailing suffix letters
    baked into the same field (no separate 'suffix' field/separator —
    confirmed real tags like 'P-203A'/'E-104B' have NO hyphen before the
    suffix letter)

Same evidence-based restructuring precedent as migration 0050's earlier
rework of instrument_index's own field shape. Only the exact seeded
default row is touched (matched by name). I/O List only —
apps.pid_checker_v2 / apps.pid_verification are not touched by, and do
not read, this migration.
"""
from django.db import migrations

SECTION = 'equipment_register'
OLD_NAME = 'Equipment Numbering — XX-XX-AAAA-XX-XX-XX (default)'
NEW_NAME = 'Equipment Numbering — [XXXX-]AAAA-NNNNN[X] (default)'

OLD_FIELDS = [
    {'key': 'area_code',        'label': 'Area Code',        'regex': r'\d{2,3}'},
    {'key': 'plant_area_code',  'label': 'Plant Area Code',  'regex': '[A-Z0-9]{2}'},
    {'key': 'equipment_code',   'label': 'Equipment Code',   'regex': '[A-Z]{2,6}'},
    {'key': 'system_code',      'label': 'System Code',      'regex': '[A-Z0-9]{2}'},
    {'key': 'unit_number',      'label': 'Unit Number',      'regex': '[A-Z0-9]{2}'},
    {'key': 'sequence_number',  'label': 'Sequence Number',  'regex': r'\d{2,4}'},
]

NEW_FIELDS = [
    {'key': 'unit_number',      'label': 'Unit Number',      'regex': r'\d{2,4}', 'optional': True},
    {'key': 'equipment_code',   'label': 'Equipment Code',   'regex': '[A-Z]{1,4}'},
    {'key': 'sequence_number',  'label': 'Sequence Number',  'regex': r'\d{1,5}[A-Z]{0,2}'},
]


def _apply(apps, name, fields):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=name).first()
    if not row:
        return
    definition = row.definition
    definition['fields'] = fields
    row.definition = definition
    row.name = NEW_NAME if fields is NEW_FIELDS else OLD_NAME
    row.save(update_fields=['definition', 'name'])
    print(f'[instrument_io_workflow] {SECTION!r} field structure replaced '
          f'({len(fields)} fields).')


def apply_forward(apps, schema_editor):
    _apply(apps, OLD_NAME, NEW_FIELDS)


def apply_backward(apps, schema_editor):
    _apply(apps, NEW_NAME, OLD_FIELDS)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0060_instrument_index_add_more_missing_codes'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
