"""Structured Procurement/Finance to project cost-ledger synchronization."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict

from .cost_ledger import rebuild_project_ledger


def sync_project_spend(project, user=None) -> Dict:
    """Rebuild the ledger; the retained name keeps the existing API stable."""
    totals = rebuild_project_ledger(project, user=user)
    return {
        'project_code': project.code,
        'matched_invoices': project.cost_ledger_entries.filter(
            entry_type='actual', status='posted', is_deleted=False,
        ).count(),
        'total_spent': totals['actual'],
        'ledger_totals': totals,
        'calculation_source': 'posted_cost_ledger',
        'skipped': False,
    }


def sync_all_projects() -> Dict:
    """Rebuild every active enterprise project's structured ledger."""
    from apps.core.project_models import Project

    projects = Project.objects.filter(is_deleted=False)
    total = 0
    total_spent = Decimal('0')
    for project in projects.iterator(chunk_size=50):
        result = sync_project_spend(project)
        total += 1
        total_spent += Decimal(result['total_spent'])
    return {'projects': total, 'total_spent': str(total_spent), 'skipped': False}
