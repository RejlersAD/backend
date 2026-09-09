"""
Seeds one active legend for the new 'instrument_symbols' section, following
the exact same pattern as the other 21 sections: single field, identity
name->name lookup table, regex matching any of the literal symbol names
(mirrors the 'actuator_symbols' legend's definition shape exactly).

created_by is copied from an existing seeded legend rather than hardcoded,
so this works the same regardless of which user owns the other 21 rows.
"""
from django.db import migrations

LOOKUP = {
    'SELECTOR SWITCH - DCS': 'SELECTOR SWITCH - DCS',
    'SELECTOR SWITCH - LOCAL': 'SELECTOR SWITCH - LOCAL',
    'ANALYSER ELEMENT(PROBE)': 'ANALYSER ELEMENT(PROBE)',
    'LOCALLY MOUNTED': 'LOCALLY MOUNTED',
    'MOUNTED ON MAIN CONTROL ROOM PANEL': 'MOUNTED ON MAIN CONTROL ROOM PANEL',
    'MOUNTED ON LOCAL PANEL': 'MOUNTED ON LOCAL PANEL',
    'MOUNTED ON BACK OF LOCAL PANEL': 'MOUNTED ON BACK OF LOCAL PANEL',
    'DISTRIBUTED CONTROL SYSTEM TEMS NORMALLY NOT ACCESSIBLE TO THE OPERATOR':
        'DISTRIBUTED CONTROL SYSTEM TEMS NORMALLY NOT ACCESSIBLE TO THE OPERATOR',
    'PROGRAMMABLE LOGIC CONTROL SYSTEM ITEMS NORMALLY ACCESSIBLE TO THE OPERATOR':
        'PROGRAMMABLE LOGIC CONTROL SYSTEM ITEMS NORMALLY ACCESSIBLE TO THE OPERATOR',
    'PROGRAMMABLE LOGIC CONTROL SYSTEM ITEMS NORMALLY NOT ACCESSIBLE TO THE OPERATOR':
        'PROGRAMMABLE LOGIC CONTROL SYSTEM ITEMS NORMALLY NOT ACCESSIBLE TO THE OPERATOR',
    'PILOT LIGHT': 'PILOT LIGHT',
    'GENERALIZED FOR UNDEFINED OR COMPLEX INTERLOCK LOGIC':
        'GENERALIZED FOR UNDEFINED OR COMPLEX INTERLOCK LOGIC',
    'EMERGENCY SHUTDOWN': 'EMERGENCY SHUTDOWN',
    'SHUTDOWN SIGNAL IN': 'SHUTDOWN SIGNAL IN',
    'SHUTDOWN SIGNAL OUT': 'SHUTDOWN SIGNAL OUT',
    'ACTUATOR RESET': 'ACTUATOR RESET',
    'SIGNAL FROM TELEMETRY': 'SIGNAL FROM TELEMETRY',
    'SIGNAL TO TELEMETRY': 'SIGNAL TO TELEMETRY',
    'DISPLACEMENT TYPE LEVEL TRANSMITTER': 'DISPLACEMENT TYPE LEVEL TRANSMITTER',
    'DIFFERENTIAL PRESSURE TYPE LEVEL TRANSMITTER': 'DIFFERENTIAL PRESSURE TYPE LEVEL TRANSMITTER',
    'LEVEL GAUGE': 'LEVEL GAUGE',
    'ULTRASONIC FLOWMETER': 'ULTRASONIC FLOWMETER',
    'TURBINE FLOWMETER': 'TURBINE FLOWMETER',
    'ROTAMETER TYPE FLOW INDICATOR': 'ROTAMETER TYPE FLOW INDICATOR',
    'VORTEX FLOW METER': 'VORTEX FLOW METER',
    'MASS FLOWMETER': 'MASS FLOWMETER',
    'ORIFICE PLATE': 'ORIFICE PLATE',
    'ORIFICE PLATE-QUICK CHANGE FITTIN': 'ORIFICE PLATE-QUICK CHANGE FITTIN',
    'POSITIVE DISPLACEMENT FLOWMETER': 'POSITIVE DISPLACEMENT FLOWMETER',
    'PIG SIGNALLER': 'PIG SIGNALLER',
    'FIRE AND GAS PANEL CONTROL ROOM MOUNTED': 'FIRE AND GAS PANEL CONTROL ROOM MOUNTED',
    'FIRE AND GAS PANEL LOCAL PANEL MOUNTED': 'FIRE AND GAS PANEL LOCAL PANEL MOUNTED',
}

DEFINITION = {
    'separator': '-',
    'fields': [{
        'key': 'instrument_symbol',
        'label': 'Instrument Symbol',
        'notes': 'Instrument symbol matched directly against Legend',
        'regex': '[A-Z0-9 /()&-]+',
        'lookup': LOOKUP,
    }],
}


def seed_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    if IOListLegendSheet.objects.filter(section='instrument_symbols').exists():
        return  # already seeded (e.g. migration re-run) — don't duplicate

    template = IOListLegendSheet.objects.exclude(created_by__isnull=True).first()
    if not template:
        return  # no existing legend to copy created_by from — nothing to seed against

    IOListLegendSheet.objects.create(
        created_by_id=template.created_by_id,
        section='instrument_symbols',
        name='Instrument Symbols — Symbol Type (default)',
        description='Instrument symbol matched directly against Legend',
        definition=DEFINITION,
        is_active=True,
    )
    print('[instrument_io_workflow] Seeded 1 legend for section=instrument_symbols.')


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0009_alter_iolistlegendsheet_section'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
