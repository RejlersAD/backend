"""
Adds the standard P&ID piping abbreviations (CSC, CSO, CV, FC, FO, NRV,
PSV, ... — valve/line operating-state and connection abbreviations) into
the existing 'piping' legend's lookup table, alongside the piping-type
entries already there (MAIN FLOW, HEAT TRACE, etc.) — additive merge, no
existing entries removed. Also widens the field's regex to allow '-'
(needed for values like SUB-SURFACE SAFETY VALVE; the regex only
constrains the matched tag text, not lookup values, but kept consistent
with the other symbol-lookup sections that already allow '-').

Only the exact seeded default row is touched (matched by name).
"""
from django.db import migrations

SECTION = 'piping'

ABBREVIATIONS = {
    'CSC': 'CAR SEAL CLOSE',
    'CSO': 'CAR SEAL OPEN',
    'CV': 'CONTROL VALVE',
    'D': 'DRAIN',
    'FB': 'FULL BORE',
    'GO': 'GEAR OPERATED',
    'HC': 'HOSE CONNECTION',
    'HCV': 'HAND CONTROL VALVE',
    'LC': 'LOCKED CLOSED',
    'LO': 'LOCKED OPEN',
    'NC': 'NORMALLY CLOSED',
    'NO': 'NORMALLY OPEN',
    'NRV': 'NON-RETURN VALVE',
    'FC': 'FAIL CLOSED',
    'FO': 'FAIL OPEN',
    'SDV': 'SHUTDOWN VALVE',
    'PSV': 'PRESSURE/VACUUM RELIEF VALVE',
    'RB': 'REDUCING BORE',
    'RO': 'RESTRICTING ORIFICE',
    'RV': 'RELIEF VALVE',
    'SC': 'SAMPLE CONNECTION',
    'SO': 'STEAMING OUT',
    'SP': 'SET PRESSURE',
    'SSSV': 'SUB-SURFACE SAFETY VALVE',
    'SSV': 'SURFACE SAFETY VALVE',
    'TSO': 'TIGHT SHUT OFF',
    'TW': 'THERMOWELL',
    'UC': 'UTILITY CONNECTION',
    'V': 'VENT',
    'HPPV': 'HIGH PRESSURE PILOT VALVE',
    'LPPV': 'LOW PRESSURE PILOT VALVE',
    'AG': 'ABOVE GROUND',
    'UG': 'UNDER GROUND',
    'ILO': 'INTERLOCK OPEN',
    'ILC': 'INTERLOCK CLOSED',
}

EXISTING_PIPING_TYPE_LOOKUP = {
    'MAIN FLOW': 'MAIN FLOW',
    'OTHERS FLOW': 'OTHERS FLOW',
    'INSULATED PIPING WITH THICKNESS': 'INSULATED PIPING WITH THICKNESS',
    'HEAT TRACE': 'HEAT TRACE',
    'PRESSURE LEAD TUBING': 'PRESSURE LEAD TUBING',
    'PIPING/SIGNAL JUNCTION': 'PIPING/SIGNAL JUNCTION',
    'LINE ABOVE GROUND (A/G)': 'LINE ABOVE GROUND (A/G)',
    'LINE UNDER GROUND (U/G)': 'LINE UNDER GROUND (U/G)',
    'PIPING CLASS': 'PIPING CLASS',
    'PIPING SPECIFICATION BREAK': 'PIPING SPECIFICATION BREAK',
}

NEW_LOOKUP = {**EXISTING_PIPING_TYPE_LOOKUP, **ABBREVIATIONS}

NAME = 'Piping — Piping Type (default)'


def add_forward(apps, schema_editor):
    PidCheckerV2LegendSheet = apps.get_model('pid_checker_v2', 'PidCheckerV2LegendSheet')
    row = PidCheckerV2LegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'piping_type':
            field['regex'] = '[A-Z0-9 /()&-]+'
            field['lookup'] = NEW_LOOKUP
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[pid_checker_v2] Added {len(ABBREVIATIONS)} piping abbreviations to the {SECTION!r} legend.')


def add_backward(apps, schema_editor):
    PidCheckerV2LegendSheet = apps.get_model('pid_checker_v2', 'PidCheckerV2LegendSheet')
    row = PidCheckerV2LegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'piping_type':
            field['regex'] = '[A-Z0-9 /()&]+'
            field['lookup'] = EXISTING_PIPING_TYPE_LOOKUP
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('pid_checker_v2', '0018_fix_line_list_field_order'),
    ]

    operations = [
        migrations.RunPython(add_forward, add_backward),
    ]
