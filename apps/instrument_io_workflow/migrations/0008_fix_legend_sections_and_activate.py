"""
No longer depends on pid_checker_v2 — see 0006_copy_legends_from_pid_
checker_v2's own docstring for why (I/O List's legend system must be
push/deploy-able completely independently of pid_checker_v2).

Originally: corrected the legends copied in by 0006 (which is now itself
a no-op — nothing is ever copied in the first place) — fixing each row's
section by exact name match and activating it, falling back to creating
a fresh row from apps.pid_checker_v2's still-intact source if 0006 had
left one out.

Kept genuinely useful, dependency-free half: if a row with one of the 21
known legend names already exists (e.g. hand-created, or carried over
from an environment that ran the OLD version of 0006 before this rework),
fix its section and activate it — self-contained, only ever reads/writes
IOListLegendSheet, no other app involved. Dropped entirely: the pid_
checker_v2 fallback that CREATED a missing row from that app's data — a
fresh I/O List deployment has no rows to fix (0006 copies nothing now),
so this migration is a safe no-op there; it only does real work in an
environment migrating up from the old copy-based 0006/0008 pair.

Irreversible on purpose, same reasoning as 0006: nothing safe to revert.
"""
from django.db import migrations

# name -> correct section, taken directly from the source
# PidCheckerV2LegendSheet rows at the time this was originally written.
NAME_TO_SECTION = {
    'Actuator Symbols — Actuator Type (default)': 'actuator_symbols',
    'Control Valve & Regulator — Control Valve Type (default)': 'control_valve_regulator',
    'Drawing Cont — Drawing Cont Type (default)': 'drawing_cont',
    'Equipment List — XX-XXX-XX (default)': 'equipment_list',
    'Equipment Symbols — Equipment Type (default)': 'equipment_symbols',
    'Flow Detector — Flow Detector Type (default)': 'flow_detector',
    'General Instrument — Instrument Bubble Type (default)': 'general_instrument',
    'Instrument Function — Instrument Function (default)': 'instrument_function',
    'Instrument Index — XX-NNNN[A] SS (default)': 'instrument_index',
    'Instrument Signal — Signal Type (default)': 'instrument_signal',
    'Instrument Typical Letter — Instrument Code (default)': 'instrument_typical_letter',
    'Limit Line — Limit Line Type (default)': 'limit_line',
    'Line List — XX-XX-XXXX-XXXX-X (default)': 'line_list',
    'Miscellaneous — Miscellaneous Type (default)': 'miscellaneous',
    'Other Specialties — Specialty Type (default)': 'other_specialties',
    'Pipe Connection — Pipe Connection Type (default)': 'pipe_connection',
    'Pipe End — Pipe End Type (default)': 'pipe_end',
    'Piping — Piping Type (default)': 'piping',
    'Scope Symbols — Scope Symbol Type (default)': 'scope_symbols',
    'Special Piping — Special Piping Type (default)': 'special_piping',
    'Valve — Valve Type (default)': 'valve',
}


def fix_sections_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    fixed = 0
    for name, section in NAME_TO_SECTION.items():
        row = IOListLegendSheet.objects.filter(name=name).first()
        if not row:
            # No pid_checker_v2 fallback anymore — a legend this migration
            # doesn't find is simply left absent; the user adds it by hand
            # via the Legend Sheets UI instead.
            continue
        row.section = section
        row.is_active = True
        row.save(update_fields=['section', 'is_active'])
        fixed += 1

    if fixed:
        print(f'[instrument_io_workflow] Corrected {fixed} legend(s).')


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0007_alter_iolistlegendsheet_section_and_more'),
    ]

    operations = [
        migrations.RunPython(fix_sections_forward, migrations.RunPython.noop),
    ]
