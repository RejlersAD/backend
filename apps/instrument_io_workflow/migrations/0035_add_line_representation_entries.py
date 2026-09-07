"""
Adds standard line-representation entries (main/secondary process lines,
pneumatic/electromagnetic signal lines, skid limit, abandoned pipe, fence,
slope lines, internal system link, continuation reference) to the
'signal_line_types' ("Line Representation" tab) legend's lookup table —
merged with, not replacing, the existing signal-type entries.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'signal_line_types'
NAME = 'Line Representation — Signal Type (default)'

NEW_ENTRIES = {
    'MAIN PROCESS LINE': 'MAIN PROCESS LINE',
    'SECONDARY PROCESS & UTILITY LINE': 'SECONDARY PROCESS & UTILITY LINE',
    'PNEUMATIC SIGNAL': 'PNEUMATIC SIGNAL',
    'ELECTROMAGNETIC OR SONIC SIGNAL (WITHOUT WIRING OR TUBING)':
        'ELECTROMAGNETIC OR SONIC SIGNAL (WITHOUT WIRING OR TUBING)',
    'SKID OR PACKAGE LIMIT': 'SKID OR PACKAGE LIMIT',
    'EXISTING PIPE TO BE ABANDONED OR REMOVED': 'EXISTING PIPE TO BE ABANDONED OR REMOVED',
    'FENCE': 'FENCE',
    'LINE TO SLOPE DOWN (WITH SLOPE ANGLE)': 'LINE TO SLOPE DOWN (WITH SLOPE ANGLE)',
    'LINE TO SLOPE DOWN (UNDEFINED)': 'LINE TO SLOPE DOWN (UNDEFINED)',
    'INTERNAL SYSTEM LINK (SOFTWARE OR DATA LINK)': 'INTERNAL SYSTEM LINK (SOFTWARE OR DATA LINK)',
    'REFERENCE TO CONTINUATION DRAWING': 'REFERENCE TO CONTINUATION DRAWING',
}


def add_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'signal_type':
            field.setdefault('lookup', {}).update(NEW_ENTRIES)
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added {len(NEW_ENTRIES)} line-representation entries to {SECTION!r}.')


def add_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'signal_type':
            for key in NEW_ENTRIES:
                field.get('lookup', {}).pop(key, None)
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0034_rename_signal_line_types_to_line_representation'),
    ]

    operations = [
        migrations.RunPython(add_forward, add_backward),
    ]
