"""
EDDR Generator (MODULE 8) — Engineering Document Deliverable Register.

Builds one register row per deliverable, pulling the IFR / Company Review /
IFA / Approval / Final-Issue dates directly from the already-generated
activity chain (so the EDDR is always internally consistent with the
schedule — no separate date computation).
"""
from __future__ import annotations

_STEP_LABELS = [
    'prepare_start', 'ifr_issue', 'company_review', 'ifa_issue', 'company_approval', 'final_issue',
]


def build_eddr(activities: list) -> list:
    """Groups deliverable-workflow activities (6 steps each) by (discipline, deliverable)."""
    grouped: dict[tuple, list] = {}
    for activity in activities:
        deliverable = activity.get('deliverable')
        if not deliverable or activity.get('discipline') in ('survey', 'hse'):
            continue
        key = (activity['discipline'], deliverable)
        grouped.setdefault(key, []).append(activity)

    rows = []
    for (discipline, deliverable), acts in grouped.items():
        acts_sorted = sorted(acts, key=lambda a: a['start_date'])
        
        # Determine current workflow status based on dates (for tracking progress)
        current_status = 'Start'  # Default to first stage
        for act in acts_sorted:
            if act.get('workflow_status'):
                current_status = act['workflow_status']
        
        row = {
            'discipline': discipline,
            'deliverable_name': deliverable,
            'wbs_code': acts_sorted[0]['wbs_code'],
            'document_status': 'Planned',
            'current_workflow_status': current_status,  # Current stage in 6-step workflow
        }
        for label, act in zip(_STEP_LABELS, acts_sorted):
            row[f'{label}_activity_id'] = act['id']
            row[f'{label}_date'] = act['finish_date']
        row['final_issue_activity_id'] = acts_sorted[-1]['id']
        row['final_issue_date'] = acts_sorted[-1]['finish_date']
        rows.append(row)

    rows.sort(key=lambda r: (r['discipline'], r['wbs_code']))
    return rows
