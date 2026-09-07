"""
NO-OP — kept only so later migrations in this app that already depend on
this migration's NAME (0007 onward) don't need renumbering.

Originally a one-time data migration that copied existing
apps.pid_checker_v2.PidCheckerV2LegendSheet rows into the new, independent
apps.instrument_io_workflow.IOListLegendSheet table as a convenience
starting point. Reworked to do nothing at all — I/O List's legend system
must be push/deploy-able completely independently of pid_checker_v2 (no
migration dependency on that app, no read of its data), so a fresh I/O
List deployment now starts with zero legend rows. Users add their own via
the Legend Sheets UI (LegendSheetsModal) instead of inheriting P&ID's
defaults automatically — see 0008_fix_legend_sections_and_activate, which
this same rework applies to for the identical reason.

Not a schema change either way (IOListLegendSheet itself was already
created by 0005) — this migration now only exists as a placeholder in the
dependency chain.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0005_iolistdocument_legend_findings_iolistlegendsheet_and_more'),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
