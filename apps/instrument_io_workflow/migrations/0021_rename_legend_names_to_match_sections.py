"""
Cosmetic follow-up to 0020's section-id renames: 0020 deliberately left
each row's `name`/`description`/`definition` untouched (per that request's
"existing legend data preserved" requirement), so 9 rows still carried
their original P&ID-sourced display name (e.g. the 'tag_register' section
— correctly labeled "Tag Register" in the UI — still contained a legend
literally named "Line List — ... (default)"). That reads as stale P&ID
content leaking into I/O List tabs, even though the section id itself was
already correct.

This migration only replaces the leading label in `name` (the text before
" — ") with the new I/O-List label — the format-spec suffix, description,
regex, and lookup tables (the actually useful content) are all left
exactly as they are. `equipment_symbols` and `instrument_typical_letter`
are excluded — their ids/labels were never renamed, so their names already
matched.
"""
from django.db import migrations

# section -> (old exact name, new name)
RENAMES = {
    'tag_register': (
        'Line List — XX-XX-XXXX-XXXX-X (default)',
        'Tag Register — XX-XX-XXXX-XXXX-X (default)',
    ),
    'equipment_register': (
        'Equipment List — XX-XXX-XX (default)',
        'Equipment Register — XX-XXX-XX (default)',
    ),
    'valve_types': (
        'Valve — Valve Type (default)',
        'Valve Types — Valve Type (default)',
    ),
    'actuator_types': (
        'Actuator Symbols — Actuator Type (default)',
        'Actuator Types — Actuator Type (default)',
    ),
    'flow_instruments': (
        'Flow Detector — Flow Detector Type (default)',
        'Flow Instruments — Flow Detector Type (default)',
    ),
    'control_valves': (
        'Control Valve & Regulator — Control Valve Type (default)',
        'Control Valves — Control Valve Type (default)',
    ),
    'signal_line_types': (
        'Instrument Signal — Signal Type (default)',
        'Signal Line Types — Signal Type (default)',
    ),
    'instrument_functions': (
        'Instrument Function — Instrument Function (default)',
        'Instrument Functions — Instrument Function (default)',
    ),
    'instrument_bubbles': (
        'General Instrument — Instrument Bubble Type (default)',
        'Instrument Bubbles — Instrument Bubble Type (default)',
    ),
}


def rename_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    for section, (old_name, new_name) in RENAMES.items():
        updated = IOListLegendSheet.objects.filter(section=section, name=old_name).update(name=new_name)
        if updated:
            print(f'[instrument_io_workflow] Renamed {section!r} legend name '
                  f'{old_name!r} -> {new_name!r} on {updated} row(s).')


def rename_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    for section, (old_name, new_name) in RENAMES.items():
        IOListLegendSheet.objects.filter(section=section, name=new_name).update(name=old_name)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0020_restructure_legend_data'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
