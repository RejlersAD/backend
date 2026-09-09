"""
Adds 2 real-world signal_type values to 'signal_types' ("Signal Types"
tab) that were confirmed missing while verifying Legend Check findings
against a real project document (document #14) after fixing its
extraction to correctly capture the Signal Type column at all:

  POTENTIAL FREE -> DIGITAL INPUT (a "potential free" / dry contact is a
      standard way to describe a digital/discrete input signal — the
      physical contact carries no voltage of its own).
  24V DC          -> DIGITAL INPUT (this document's own convention for
      describing a powered digital input by its supply voltage).

Unlike migration 0056 (which only added lookup entries — instrument_index
had no per-value regex to fight), this field's existing regex
('[A-Z]{2,3}') is a strict 2-3 letter code and would reject both of these
before the lookup dict is even consulted (confirmed directly: extraction
surfaced "does not match the expected format for Signal Type" for both,
not a lookup-membership error). Widened to accept letters, digits,
spaces, and slashes up to 21 characters — still rejects genuinely
malformed input, just no longer assumes every signal type is a short
DCS-style code.

Only the exact seeded default row is touched (matched by name), same
pattern as migrations 0054/0056. I/O List only — apps.pid_checker_v2 /
apps.pid_verification are not touched by, and do not read, this
migration.
"""
from django.db import migrations

SECTION = 'signal_types'
NAME = 'Signal Types — Signal Type (default)'

OLD_REGEX = '[A-Z]{2,3}'
NEW_REGEX = '[A-Z0-9][A-Z0-9 /]{1,20}'

NEW_CODES = {
    'POTENTIAL FREE': 'DIGITAL INPUT',
    '24V DC':         'DIGITAL INPUT',
}


def apply_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'signal_type':
            field['lookup'] = {**field.get('lookup', {}), **NEW_CODES}
            field['regex'] = NEW_REGEX
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added {len(NEW_CODES)} signal_type codes to '
          f'{SECTION!r}: {sorted(NEW_CODES)}; widened regex to accept free-text signal types')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'signal_type':
            for code in NEW_CODES:
                field.get('lookup', {}).pop(code, None)
            field['regex'] = OLD_REGEX
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0056_instrument_index_add_missing_codes'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
