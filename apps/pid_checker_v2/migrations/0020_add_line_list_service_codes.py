"""
Adds new service codes to the 'line_list' legend's service_code lookup
table — merged with, not replacing, the existing 38 entries (no existing
key is removed, per the explicit requirement).

6 of the supplied codes conflict in MEANING (not just wording) with an
already-established entry, not merely add to it:
    HC: existing 'HYDROCARBON LIQUID'  vs supplied 'HYDROCARBON CONDENSATE'
    BD: existing 'BLOWDOWN'            vs supplied 'BLOWDOWN DRAIN'
    DF: existing 'DIESEL OIL'          vs supplied 'DIESEL FUEL'
    DW: existing 'FRESH WATER'         vs supplied 'POTABLE WATER'
    RW: existing 'REJECT WATER'        vs supplied 'RAW WATER'
    SW: existing 'SEAWATER'            vs supplied 'SCAD WATER'
For these 6, the EXISTING value is kept unchanged (not overwritten) —
'don't remove existing codes' is read here as also not silently replacing
an established meaning; SW='SEAWATER' in particular is an extremely
standard P&ID code and 'SCAD WATER' is very likely a transcription slip.
PW's supplied wording ('PRODUCED WATER (FROM PROCESS SOURCES)') is a
compatible elaboration of the existing 'PRODUCED WATER', not a
contradiction, so it's applied. Every other supplied code that has no
existing entry is added as-is.

Only the exact seeded default row is touched (matched by name).

pid_checker_v2 only — apps.instrument_io_workflow is not touched by, and
does not read, this migration.
"""
from django.db import migrations

SECTION = 'line_list'
NAME = 'Line List — XX-AA-XXXX-XXXXXX-X-X (default)'

# New codes with no existing entry — added as-is.
NEW_CODES = {
    'D': 'DRAINS (GENERAL)',
    'GD': 'GLYCOL DRAIN',
    'PD': 'PROCESS DRAIN',
    'SD': 'SEWAGE DRAIN',
    'HF': 'HELIFUEL',
    'MF': 'MOTOR GASOLINE FUEL',
    'BG': 'BLANKET GAS',
    'CG': 'CO2',
    'IG': 'INJECTION GAS',
    'PG': 'PROCESS GAS',
    'H': 'HALON',
    'NN': 'NITROGEN',
    'HO': 'HYDRAULIC OIL',
    'LO': 'LUBE OIL',
    'PO': 'PROCESS OIL (ALL CRUDE STREAMS)',
    'SO': 'SEAL OIL',
    'R': 'RELIEF',
    'V': 'VENT',
    'AW': 'AQUIFER WATER',
    'CW': 'COOLING WATER',
    'WW': 'WASH WATER',
    'Z': 'CHEMICALS (GENERAL)',
    'AZ': 'ASPHALTENE INHIBITOR',
    'BZ': 'BIOCIDE',
    'CZ': 'FLOCCULANT / COAGULANT',
    'DZ': 'DEMULSIFIER',
    'FZ': 'FOAM COMPOUND',
    'IZ': 'CORROSION INHIBITOR',
    'OZ': 'OXYGEN SCAVENGER',
    'PZ': 'PH CONTROL CHEMICAL',
    'SZ': 'SCALE INHIBITOR',
    'XZ': 'WAX INHIBITOR',
}

# Existing key, compatible elaboration — safe to apply.
UPDATED_COMPATIBLE = {
    'PW': 'PRODUCED WATER (FROM PROCESS SOURCES)',
}


def apply_forward(apps, schema_editor):
    PidCheckerV2LegendSheet = apps.get_model('pid_checker_v2', 'PidCheckerV2LegendSheet')
    row = PidCheckerV2LegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'service_code':
            lookup = field.setdefault('lookup', {})
            before = set(lookup.keys())
            lookup.update(NEW_CODES)
            lookup.update(UPDATED_COMPATIBLE)
            added = set(lookup.keys()) - before
            print(f'[pid_checker_v2] Added {len(added)} new service code(s) to {SECTION!r}: {sorted(added)}')
    row.definition = definition
    row.save(update_fields=['definition'])


def apply_backward(apps, schema_editor):
    PidCheckerV2LegendSheet = apps.get_model('pid_checker_v2', 'PidCheckerV2LegendSheet')
    row = PidCheckerV2LegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'service_code':
            lookup = field.get('lookup', {})
            for key in NEW_CODES:
                lookup.pop(key, None)
            lookup['PW'] = 'PRODUCED WATER'
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('pid_checker_v2', '0019_add_piping_abbreviations'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
