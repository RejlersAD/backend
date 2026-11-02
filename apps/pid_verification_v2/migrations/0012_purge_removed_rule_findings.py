"""Data migration (30 Oct 2026): delete findings of rules that were
removed or re-classified as non-findings:

  CON-001 / CON-002  "possible orphan" instrument/valve — graph
                     connectivity from text alone is not reliable
                     (326 + 216 false majors on Borouge Package 2).
  ANN-001            pressure annotation — information, not a finding.
  XREF-001           multi-page tag occurrence — cross-sheet information,
                     now shown in the "Tags across sheets" table in
                     Drawing sets (computed from PIDVTagIndex).
"""
from django.db import migrations


_REMOVED_RULE_IDS = ('CON-001', 'CON-002', 'ANN-001', 'XREF-001')


def purge_removed_rule_findings(apps, schema_editor):
    PIDVFinding = apps.get_model('pid_verification_v2', 'PIDVFinding')
    PIDVFinding.objects.filter(rule_id__in=_REMOVED_RULE_IDS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification_v2', '0011_pidvchecklistoverride'),
    ]

    operations = [
        migrations.RunPython(purge_removed_rule_findings, migrations.RunPython.noop),
    ]
