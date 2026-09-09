"""
Adds 8 real-world signal_type values to 'signal_types' ("Signal Types"
tab) — common voltage/current-loop signal descriptions found in real I/O
List documents that the lookup didn't cover:

  POTENTIAL FREE -> DIGITAL INPUT   (dry contact input)
  24V DC          -> DIGITAL INPUT   (powered digital input, spaced form)
  24VDC           -> DIGITAL INPUT   (same, unspaced form — a different
                                      normalized key from '24V DC', kept
                                      as its own entry since documents
                                      write it both ways)
  110VAC          -> DIGITAL INPUT
  220VAC          -> DIGITAL INPUT
  4-20 MA         -> ANALOG INPUT    (standard analog current loop)
  4-20MA          -> ANALOG INPUT    (same, unspaced form)
  0-20 MA         -> ANALOG INPUT

POTENTIAL FREE/24V DC were already added by migration 0057 (which also
widened the field's regex from '[A-Z]{2,3}' to accept free-text signal
descriptions) — re-added here too (harmless, same value) so this
migration is self-contained and its 6 new entries don't depend on running
0057 first in some future replay/squash scenario. Confirmed all 8 values
already pass 0057's widened regex after normalize_lookup_value's
hyphen/whitespace collapsing — no further regex change needed here.

Only the exact seeded default row is touched (matched by name), same
pattern as migrations 0054/0056/0057. I/O List only —
apps.pid_checker_v2 / apps.pid_verification are not touched by, and do
not read, this migration.
"""
from django.db import migrations

SECTION = 'signal_types'
NAME = 'Signal Types — Signal Type (default)'

NEW_CODES = {
    'POTENTIAL FREE': 'DIGITAL INPUT',
    '24V DC':         'DIGITAL INPUT',
    '24VDC':          'DIGITAL INPUT',
    '110VAC':         'DIGITAL INPUT',
    '220VAC':         'DIGITAL INPUT',
    '4-20 MA':        'ANALOG INPUT',
    '4-20MA':         'ANALOG INPUT',
    '0-20 MA':        'ANALOG INPUT',
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
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added/confirmed {len(NEW_CODES)} signal_type codes '
          f'in {SECTION!r}: {sorted(NEW_CODES)}')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'signal_type':
            # Only remove the 6 entries genuinely new to THIS migration —
            # POTENTIAL FREE/24V DC stay (they belong to 0057, which this
            # migration doesn't own reversal of).
            for code in ('24VDC', '110VAC', '220VAC', '4-20 MA', '4-20MA', '0-20 MA'):
                field.get('lookup', {}).pop(code, None)
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0057_signal_types_add_free_text_codes'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
