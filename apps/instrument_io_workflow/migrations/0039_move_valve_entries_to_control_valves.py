"""
Corrects a mistake from 0038: the 16 control/regulating/safety valve
entries were added to the wrong tab ('valve_types' / "Control Values").
They belong on 'control_valves' / "Control Valves" instead.

This migration:
1. Removes the 16 entries from 'valve_types' and restores its regex to
   what 0038 changed it from ('[A-Z0-9 ()&]+') — its other, correctly-
   placed entries are untouched.
2. Adds the same 16 entries to 'control_valves'. No regex change needed
   there — its field already allows '/' and '-'
   ('[A-Z0-9 /()&-]+').

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

VALVE_TYPES_SECTION = 'valve_types'
VALVE_TYPES_NAME = 'Control Values — Valve Type (default)'
VALVE_TYPES_OLD_REGEX = '[A-Z0-9 ()&]+'

CONTROL_VALVES_SECTION = 'control_valves'
CONTROL_VALVES_NAME = 'Control Valves — Control Valve Type (default)'

MOVED_ENTRIES = {
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


def move_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    vt_row = IOListLegendSheet.objects.filter(section=VALVE_TYPES_SECTION, name=VALVE_TYPES_NAME).first()
    if vt_row:
        definition = vt_row.definition
        for field in definition.get('fields', []):
            if field.get('key') == 'valve_type':
                field['regex'] = VALVE_TYPES_OLD_REGEX
                for key in MOVED_ENTRIES:
                    field.get('lookup', {}).pop(key, None)
        vt_row.definition = definition
        vt_row.save(update_fields=['definition'])
        print(f'[instrument_io_workflow] Removed {len(MOVED_ENTRIES)} entries from {VALVE_TYPES_SECTION!r}.')

    cv_row = IOListLegendSheet.objects.filter(section=CONTROL_VALVES_SECTION, name=CONTROL_VALVES_NAME).first()
    if cv_row:
        definition = cv_row.definition
        for field in definition.get('fields', []):
            if field.get('key') == 'control_valve_type':
                field.setdefault('lookup', {}).update(MOVED_ENTRIES)
        cv_row.definition = definition
        cv_row.save(update_fields=['definition'])
        print(f'[instrument_io_workflow] Added {len(MOVED_ENTRIES)} entries to {CONTROL_VALVES_SECTION!r}.')


def move_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    vt_row = IOListLegendSheet.objects.filter(section=VALVE_TYPES_SECTION, name=VALVE_TYPES_NAME).first()
    if vt_row:
        definition = vt_row.definition
        for field in definition.get('fields', []):
            if field.get('key') == 'valve_type':
                field['regex'] = '[A-Z0-9 ()&/-]+'
                field.setdefault('lookup', {}).update(MOVED_ENTRIES)
        vt_row.definition = definition
        vt_row.save(update_fields=['definition'])

    cv_row = IOListLegendSheet.objects.filter(section=CONTROL_VALVES_SECTION, name=CONTROL_VALVES_NAME).first()
    if cv_row:
        definition = cv_row.definition
        for field in definition.get('fields', []):
            if field.get('key') == 'control_valve_type':
                for key in MOVED_ENTRIES:
                    field.get('lookup', {}).pop(key, None)
        cv_row.definition = definition
        cv_row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0038_add_control_values_valve_entries'),
    ]

    operations = [
        migrations.RunPython(move_forward, move_backward),
    ]
