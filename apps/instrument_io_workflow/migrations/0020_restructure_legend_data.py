"""
Data half of the I/O List legend section restructure (schema half in
0019). I/O List only — apps.pid_checker_v2 is not touched by, and does not
read, this migration.

1. Deletes all legend rows for 9 sections that no longer exist:
   piping, pipe_connection, pipe_end, special_piping, limit_line,
   scope_symbols, drawing_cont, other_specialties, miscellaneous.
   Irreversible by design (the data is genuinely gone) — reverse restores
   only the renames below, not these deletions.

2. Renames 8 sections in place (updates the `section` field only —
   name/description/definition/is_active are left exactly as they were,
   so existing legend data is preserved):
   line_list -> tag_register, equipment_list -> equipment_register,
   valve -> valve_types, actuator_symbols -> actuator_types,
   flow_detector -> flow_instruments,
   control_valve_regulator -> control_valves,
   instrument_function -> instrument_functions,
   general_instrument -> instrument_bubbles.

3. Renames instrument_signal -> 'signal_line_types' (NOT 'signal_types' as
   literally requested) — 'signal_types' was already claimed one message
   earlier by a different, newly-added I/O-channel-type tab
   (AI/AO/DI/DO/... lookup). Using the same id for both would collide:
   only one legend per (user, section) may be active at a time, so
   whichever was renamed second would either fail against that constraint
   or silently make the other section's default legend inactive/
   inaccessible. This keeps both tabs' data intact under distinct ids.
"""
from django.db import migrations

DELETE_SECTIONS = [
    'piping', 'pipe_connection', 'pipe_end', 'special_piping',
    'limit_line', 'scope_symbols', 'drawing_cont', 'other_specialties',
    'miscellaneous',
]

RENAMES = [
    ('line_list', 'tag_register'),
    ('equipment_list', 'equipment_register'),
    ('valve', 'valve_types'),
    ('actuator_symbols', 'actuator_types'),
    ('flow_detector', 'flow_instruments'),
    ('control_valve_regulator', 'control_valves'),
    ('instrument_function', 'instrument_functions'),
    ('general_instrument', 'instrument_bubbles'),
    ('instrument_signal', 'signal_line_types'),
]


def restructure_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    deleted, _ = IOListLegendSheet.objects.filter(section__in=DELETE_SECTIONS).delete()
    print(f'[instrument_io_workflow] Deleted {deleted} legend row(s) across '
          f'{len(DELETE_SECTIONS)} removed section(s).')

    for old, new in RENAMES:
        updated = IOListLegendSheet.objects.filter(section=old).update(section=new)
        if updated:
            print(f'[instrument_io_workflow] Renamed section {old!r} -> {new!r} '
                  f'on {updated} row(s).')


def restructure_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    for old, new in RENAMES:
        IOListLegendSheet.objects.filter(section=new).update(section=old)
    # Deleted rows (DELETE_SECTIONS) cannot be restored — that data is gone.


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0019_restructure_legend_sections'),
    ]

    operations = [
        migrations.RunPython(restructure_forward, restructure_backward),
    ]
