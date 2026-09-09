"""
I/O List counterpart to apps.pid_checker_v2's 0019 migration — adds the
same standard piping abbreviations to the 'piping' legend's lookup table,
keeping I/O List's independent copy in structural parity (data stays
independently editable; this is a one-time content addition, not an
ongoing link between the two systems).
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
    'HEAT TRACE': 'HEAT TRACE',
    'OTHERS FLOW': 'OTHERS FLOW',
    'PIPING CLASS': 'PIPING CLASS',
    'PRESSURE LEAD TUBING': 'PRESSURE LEAD TUBING',
    'PIPING/SIGNAL JUNCTION': 'PIPING/SIGNAL JUNCTION',
    'LINE ABOVE GROUND (A/G)': 'LINE ABOVE GROUND (A/G)',
    'LINE UNDER GROUND (U/G)': 'LINE UNDER GROUND (U/G)',
    'PIPING SPECIFICATION BREAK': 'PIPING SPECIFICATION BREAK',
    'INSULATED PIPING WITH THICKNESS': 'INSULATED PIPING WITH THICKNESS',
}

NEW_LOOKUP = {**EXISTING_PIPING_TYPE_LOOKUP, **ABBREVIATIONS}

NAME = 'Piping — Piping Type (default)'


def add_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'piping_type':
            field['regex'] = '[A-Z0-9 /()&-]+'
            field['lookup'] = NEW_LOOKUP
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added {len(ABBREVIATIONS)} piping abbreviations to the {SECTION!r} legend.')


def add_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
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
        ('instrument_io_workflow', '0014_instrument_index_5_field_format'),
    ]

    operations = [
        migrations.RunPython(add_forward, add_backward),
    ]
