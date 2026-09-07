"""
Relabels 'signal_line_types' to "Line Representation" — id unchanged, only
the display label. Updates the seeded legend row's `name` prefix to match;
description/definition untouched.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'signal_line_types'
OLD_NAME = 'Signal Line Types — Signal Type (default)'
NEW_NAME = 'Line Representation — Signal Type (default)'


def rename_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    updated = IOListLegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(name=NEW_NAME)
    print(f'[instrument_io_workflow] Relabelled {SECTION!r} legend name on {updated} row(s).')


def rename_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0033_relabel_signal_line_types'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
