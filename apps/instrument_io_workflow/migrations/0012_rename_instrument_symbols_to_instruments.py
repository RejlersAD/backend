"""
Renames the 'instrument_symbols' section's display name from
'Instrument Symbols' to 'Instruments' — the original label was too easily
confused with the pre-existing, different 'Instrument Signal' section.
Only the display text changes; the section id (instrument_symbols) and all
32 lookup entries are untouched.
"""
from django.db import migrations


def rename_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    (IOListLegendSheet.objects
        .filter(section='instrument_symbols', name='Instrument Symbols — Symbol Type (default)')
        .update(name='Instruments — Symbol Type (default)'))


def rename_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    (IOListLegendSheet.objects
        .filter(section='instrument_symbols', name='Instruments — Symbol Type (default)')
        .update(name='Instrument Symbols — Symbol Type (default)'))


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0011_alter_iolistlegendsheet_section'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
