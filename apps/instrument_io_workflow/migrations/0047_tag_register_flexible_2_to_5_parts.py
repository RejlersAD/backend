"""
Updates 'tag_register' ("Instrument Tagging" tab) to accept flexible
2-to-5-part tag formats: area_code and plant_area_code become optional
leading fields, instrument_classification and sequence_number stay
required, suffix stays an optional trailing field.

This required a companion fix to services/legend_comparison.py's
compile_legend() — its regex-building algorithm assumed only TRAILING
fields could be optional (a required field always demanded an
unconditional leading separator). With optional fields now leading the
sequence too, skipping both area_code and plant_area_code left no way to
supply the separator instrument_classification always required, so
'XHSC-9502' (2-part) could never match no matter what. Fixed generally
(not special-cased to this one section) so any future leading-optional
field works the same way; verified against every other currently active
legend to confirm zero regressions (none of them have leading-optional
fields, so their compiled patterns are unaffected).

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'tag_register'
OLD_NAME = 'Instrument Tagging — NNN-AAAA-XXXXB (default)'
NEW_NAME = 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)'
NEW_DESCRIPTION = (
    'Flexible instrument tag numbering, 2 to 5 parts: optional area code, '
    'optional plant area code, required instrument classification code, '
    'required sequence number (+ optional letter), optional trailing '
    'suffix (e.g. XHSC-9502, 113-XHSC-9502, 11-13-XHSC-9502, '
    '11-13-XHSC-9502-A).'
)

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
    # Not restoring the old 3-field-only definition on reverse — the fix
    # to compile_legend() itself stays either way, and this row's old
    # definition is fully captured in migration 0016's own reverse.
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0046_seed_inline_equipment_legend'),
    ]

    operations = [
        migrations.RunPython(update_forward, update_backward),
    ]
