"""
Widens the 'sequence_number' field regex in 'instrument_index' and
'well_instrument_tagging' from a 3-5 digit minimum ('\\d{3,5}[A-Z]?' /
'\\d{3,5}') to a 2-5 digit minimum ('\\d{2,5}[A-Z]?' / '\\d{2,5}').

Found while fixing the exact same overly-strict minimum-digit-count bug
in services/io_table_extractor.py's _OCR_TAG_RE and _LOOP_ROW_TAG_RE
(confirmed via a real, explicit "10-PSV-15B" test case — sequence '15'
is only 2 digits). The legend's own format-validation regex had the
identical flaw: even after the extractor fix correctly extracts a
genuine 2-digit-loop tag like '10-PSV-15B', Legend Check would still
flag it as "not a recognised format" purely because this regex demanded
a 3-digit minimum — a real, connected gap, not a hypothetical one. Real
ISA loop/sequence numbers are commonly 2-4 digits, not always 3+.

Updates every row (active or not) in either section whose
'sequence_number' field still has the OLD regex exactly — a row a user
already customized to something else is left untouched. Only
instrument_io_workflow; apps.pid_checker_v2 is not touched by, and does
not read, this migration.
"""
from django.db import migrations

FIELDS_TO_WIDEN = [
    ('instrument_index', 'sequence_number', r'\d{3,5}[A-Z]?', r'\d{2,5}[A-Z]?'),
    ('well_instrument_tagging', 'sequence_number', r'\d{3,5}', r'\d{2,5}'),
]


def _apply(apps, old_to_new: bool):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    for section, field_key, old_regex, new_regex in FIELDS_TO_WIDEN:
        source, target = (old_regex, new_regex) if old_to_new else (new_regex, old_regex)
        updated = 0
        for row in IOListLegendSheet.objects.filter(section=section):
            definition = row.definition or {}
            changed = False
            for field in definition.get('fields', []):
                if field.get('key') == field_key and field.get('regex') == source:
                    field['regex'] = target
                    changed = True
            if changed:
                row.definition = definition
                row.save(update_fields=['definition'])
                updated += 1
        direction = 'widened' if old_to_new else 'reverted'
        print(f'[instrument_io_workflow] {direction} {field_key!r} regex on {updated} '
              f'{section!r} legend row(s).')


def apply_forward(apps, schema_editor):
    _apply(apps, old_to_new=True)


def apply_backward(apps, schema_editor):
    _apply(apps, old_to_new=False)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0058_signal_types_add_more_codes'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
