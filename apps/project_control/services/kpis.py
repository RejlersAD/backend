"""Phase 1 — Cost KPIs.

Computes headline numbers exclusively from posted cost-ledger entries.

Pure-Python; never touches DB outside the call.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict

from ..models import Estimate, CostSnapshot
from .cost_ledger import ledger_summary


def _d(x) -> Decimal:
    if x is None:
        return Decimal('0')
    return x if isinstance(x, Decimal) else Decimal(str(x))


def compute_project_kpis(project) -> Dict:
    """Return the canonical cost-dashboard KPI dict for a project."""
    ledger = ledger_summary(project)
    budget = ledger['budget']
    spent = ledger['spent']
    committed = ledger['committed']
    remaining = ledger['remaining']
    utilisation_pct = ledger['utilisation_pct']

    latest_snapshot = (
        CostSnapshot.objects.filter(project=project, is_deleted=False)
        .order_by('-period_end').first()
    )
    eac = _d(latest_snapshot.eac) if latest_snapshot and latest_snapshot.eac else None
    cpi = latest_snapshot.cpi if latest_snapshot else None
    spi = latest_snapshot.spi if latest_snapshot else None

    return {
        'project_id': project.id,
        'project_code': project.code,
        'project_name': project.name,
        'currency': project.currency or 'AED',
        'budget':     str(budget),
        'spent':      str(spent),
        'committed':  str(committed),
        'remaining':  str(remaining),
        'available_to_commit': str(ledger['available_to_commit']),
        'commitment_remaining': str(ledger['commitment_remaining']),
        'ledger_entry_count': ledger['entry_count'],
        'calculation_source': 'posted_cost_ledger',
        'utilisation_pct': round(utilisation_pct, 2),
        'progress_pct':    project.progress or 0,
        'forecast': {
            'eac': str(eac) if eac is not None else None,
            'cpi': cpi,
            'spi': spi,
            'snapshot_date': latest_snapshot.period_end.isoformat() if latest_snapshot else None,
        },
        'estimate_counts': {
            'total':    Estimate.objects.filter(project=project, is_deleted=False).count(),
            'approved': Estimate.objects.filter(project=project, status='approved', is_deleted=False).count(),
            'draft':    Estimate.objects.filter(project=project, status='draft', is_deleted=False).count(),
        },
    }
