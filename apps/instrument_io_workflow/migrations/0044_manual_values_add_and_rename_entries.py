"""
Updates 'valve_types' ("Manual Values" tab) lookup content:
- Adds 3 new entries: VALVE (BASIC SYMBOL), PLUG VALVE, CHECK VALVE (WAFER
  TYPE). (User wrote "VALUE" for two of these — corrected to "VALVE",
  consistent with earlier valve-tab entries and the tab's own subject
  matter.)
- Renames the existing 'CHECK VALVE' entry to 'CHECK VALVE (SWING TYPE)'
  to distinguish it from the new wafer-type entry.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'valve_types'
NAME = 'Manual Values — Valve Type (default)'

NEW_ENTRIES = {
    'VALVE (BASIC SYMBOL)': 'VALVE (BASIC SYMBOL)',
    'PLUG VALVE': 'PLUG VALVE',
    'CHECK VALVE (WAFER TYPE)': 'CHECK VALVE (WAFER TYPE)',
}
OLD_CHECK_VALVE_KEY = 'CHECK VALVE'
NEW_CHECK_VALVE_KEY = 'CHECK VALVE (SWING TYPE)'


def apply_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'valve_type':
            lookup = field.setdefault('lookup', {})
            lookup.pop(OLD_CHECK_VALVE_KEY, None)
            lookup[NEW_CHECK_VALVE_KEY] = NEW_CHECK_VALVE_KEY
            lookup.update(NEW_ENTRIES)
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Updated {SECTION!r} lookup: renamed CHECK VALVE, added {len(NEW_ENTRIES)} entries.')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'valve_type':
            lookup = field.setdefault('lookup', {})
            for key in NEW_ENTRIES:
                lookup.pop(key, None)
            lookup.pop(NEW_CHECK_VALVE_KEY, None)
            lookup[OLD_CHECK_VALVE_KEY] = OLD_CHECK_VALVE_KEY
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0043_rename_values_to_manual_values'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
