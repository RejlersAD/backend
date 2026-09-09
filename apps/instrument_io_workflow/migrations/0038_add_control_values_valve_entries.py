"""
Adds 16 control/regulating/safety valve entries to the 'valve_types'
("Control Values" tab) legend's lookup table — merged with, not replacing,
the existing basic valve-type entries.

Also widens the field's regex from '[A-Z0-9 ()&]+' to '[A-Z0-9 ()&/-]+' —
several of the new entries contain '/' (e.g. 'PRESSURE REDUCING /
REGULATING VALVE') and '-' (e.g. 'SUB-SURFACE SAFETY VALVE'), neither of
which the old charset allowed, so those tags would otherwise fail format
matching before ever reaching the lookup check.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'valve_types'
NAME = 'Control Values — Valve Type (default)'

NEW_ENTRIES = {
    'PRESSURE REDUCING / REGULATING VALVE - SELF CONTAINED':
        'PRESSURE REDUCING / REGULATING VALVE - SELF CONTAINED',
    'BACK PRESSURE REDUCING / REGULATING VALVE - SELF CONTAINED':
        'BACK PRESSURE REDUCING / REGULATING VALVE - SELF CONTAINED',
    'CONTROL VALVE OPEN ON AIR FAIL': 'CONTROL VALVE OPEN ON AIR FAIL',
    'CONTROL VALVE CLOSED ON AIR FAIL': 'CONTROL VALVE CLOSED ON AIR FAIL',
    'DIAPHRAGM OPERATED VALVE': 'DIAPHRAGM OPERATED VALVE',
    'PISTON OPERATED VALVE': 'PISTON OPERATED VALVE',
    'MOTOR OPERATED VALVE': 'MOTOR OPERATED VALVE',
    'SHUTDOWN VALVE': 'SHUTDOWN VALVE',
    'SOLENOID OPERATED VALVE': 'SOLENOID OPERATED VALVE',
    'VACUUM SAFETY OR RELIEF VALVE': 'VACUUM SAFETY OR RELIEF VALVE',
    'PRESSURE SAFETY OR RELIEF VALVE OR THERMAL SAFETY VALVE':
        'PRESSURE SAFETY OR RELIEF VALVE OR THERMAL SAFETY VALVE',
    'PRESSURE / VACUUM SAFETY OR RELIEF VALVE': 'PRESSURE / VACUUM SAFETY OR RELIEF VALVE',
    'SURFACE SAFETY VALVE': 'SURFACE SAFETY VALVE',
    'SURFACE CONTROLLED SUB-SURFACE SAFETY VALVE': 'SURFACE CONTROLLED SUB-SURFACE SAFETY VALVE',
    'DIFFERENTIAL PRESSURE REDUCING / REGULATING VALVE':
        'DIFFERENTIAL PRESSURE REDUCING / REGULATING VALVE',
    'ADJUSTABLE CHOKE VALVE': 'ADJUSTABLE CHOKE VALVE',
}

OLD_REGEX = '[A-Z0-9 ()&]+'
NEW_REGEX = '[A-Z0-9 ()&/-]+'


def add_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'valve_type':
            field['regex'] = NEW_REGEX
            field.setdefault('lookup', {}).update(NEW_ENTRIES)
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added {len(NEW_ENTRIES)} valve entries to {SECTION!r}.')


def add_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'valve_type':
            field['regex'] = OLD_REGEX
            for key in NEW_ENTRIES:
                field.get('lookup', {}).pop(key, None)
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0037_rename_valve_types_to_control_values'),
    ]

    operations = [
        migrations.RunPython(add_forward, add_backward),
    ]
