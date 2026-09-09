"""
Relabels 'tag_register' to "Instrument Tagging" — id unchanged, only the
display label. Updates the seeded legend row's `name` prefix to match
(same cosmetic-rename pattern as 0021/0024); description/definition
untouched.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

SECTION = 'tag_register'
OLD_NAME = 'Tag Register — NNN-AAAA-XXXXB (default)'
NEW_NAME = 'Instrument Tagging — NNN-AAAA-XXXXB (default)'


def rename_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    updated = IOListLegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(name=NEW_NAME)
    print(f'[instrument_io_workflow] Relabelled {SECTION!r} legend name on {updated} row(s).')


def rename_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    IOListLegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0027_seed_well_and_line_legends'),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
