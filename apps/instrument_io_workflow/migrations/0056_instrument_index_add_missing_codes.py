"""
Adds 4 real instrument-classification codes to 'instrument_index'
("Instrument Tagging" tab) that were confirmed missing while verifying
Legend Check findings against a real project document (document #14,
"HABSHAN & DIVERT EXCESS MP FUEL GAS PROJECT" I/O List) — each code was
present as a real tag on that document (113-XHSC-9501B, 113-XHSC-9502A,
113-XHSO-9501B, 113-XHSO-9502A, 113-XY-9501, 113-XY-9502, 113-PDT-3194)
and was being flagged as "not recognised" purely because the 225-entry
ISA sheet from migration 0054 didn't happen to include them, not because
they're invalid conventions:

  XHSC / XHSO — 'HAND SWITCH CLOSE' / 'HAND SWITCH OPEN'. The base code
      XHS ('SELECTOR SWITCH - HAND OPERATED') is already in the lookup,
      and the directional-suffix pattern (base + C/O for Close/Open) is
      already established by the existing XZSC/XZSO pair ('POSITION
      SWITCH — CLOSED'/'OPEN') — this is the same convention applied to
      a different base function, not a new one.

  XY — 'MISCELLANEOUS RELAY / SOLENOID' (used on the document for a
      solenoid valve's relay function, tag 113-XY-9501/-9502). The bare
      "_Y" = relay/compute/convert-function suffix is already extensively
      used in this same lookup (FY, HY, LY, PY, TY, ZY, BDY, SDY, DPZY
      are all already present) — X (miscellaneous/binary) + Y fits that
      exact established pattern.

  PDT — 'PRESSURE DIFFERENTIAL TRANSMITTER'. The lookup already has DPT
      for the same instrument concept (Differential-Pressure-Transmitter
      letter ordering) — PDT is the Pressure-Differential-Transmitter
      ordering, a different but equally standard convention different EPC
      firms use for the same instrument. Added alongside DPT (not
      replacing it) since both orderings are legitimately in use across
      real projects — this document's own tags use PDT.

Only the exact seeded default row is touched (matched by name), same as
migration 0054. I/O List only — apps.pid_checker_v2 / apps.pid_verification
are not touched by, and do not read, this migration.
"""
from django.db import migrations

SECTION = 'instrument_index'
NAME = 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)'

NEW_CODES = {
    'XHSC': 'HAND SWITCH CLOSE',
    'XHSO': 'HAND SWITCH OPEN',
    'XY':   'MISCELLANEOUS RELAY / SOLENOID',
    'PDT':  'PRESSURE DIFFERENTIAL TRANSMITTER',
}


def apply_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'instrument_classification':
            field['lookup'] = {**field.get('lookup', {}), **NEW_CODES}
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added {len(NEW_CODES)} instrument_classification '
          f'codes to {SECTION!r}: {sorted(NEW_CODES)}')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'instrument_classification':
            for code in NEW_CODES:
                field.get('lookup', {}).pop(code, None)
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0055_add_document_type'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
