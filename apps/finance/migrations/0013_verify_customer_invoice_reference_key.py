"""Retained migration-history node; operational key repair is retired.

Source snapshots no longer require unique operational invoice IDs. Existing
duplicate or null IDs must be reconciled separately, without changing invoice
records as a side effect of deploying reporting. Keep this node for databases
where the original verification already ran; 0014 detaches its legacy source FK.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('finance', '0012_receivables_source_snapshot')]
    operations = []
