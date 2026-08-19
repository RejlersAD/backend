"""
Validation Engine (MODULE 11) — deterministic rule-based schedule QA.

Every rule below maps to a specific ADNOC RFT-Appendix-4 / spec requirement.
Rules never mutate the schedule — they only report pass/warning/critical
issues for the user to review before export.
"""
from __future__ import annotations

from ..config import (
    DISCIPLINE_DEFAULT_DELIVERABLES, LEVEL4_MAX_ACTIVITY_DURATION_DAYS, MAX_TOTAL_FLOAT_DAYS,
)


def _issue(rule: str, severity: str, message: str, activity_id: str | None = None) -> dict:
    return {'rule': rule, 'severity': severity, 'message': message, 'activity_id': activity_id}


def validate(project, wbs: list, activities: list, eddr: list, intelligence: dict) -> list:
    issues: list[dict] = []

    # 1. No negative float.
    negative_float = [a for a in activities if (a.get('total_float_days') or 0) < 0]
    if negative_float:
        issues.append(_issue('negative_float', 'critical',
                              f'{len(negative_float)} activities have negative total float.'))
    else:
        issues.append(_issue('negative_float', 'pass', 'No activities with negative total float.'))

    # 2. Max total float (21 working days per RFT Appendix 4).
    over_float = [a for a in activities if (a.get('total_float_days') or 0) > MAX_TOTAL_FLOAT_DAYS]
    if over_float:
        issues.append(_issue(
            'max_total_float', 'warning',
            f'{len(over_float)} activities exceed the {MAX_TOTAL_FLOAT_DAYS}-working-day max total float rule.',
        ))
    else:
        issues.append(_issue('max_total_float', 'pass', f'All activities are within {MAX_TOTAL_FLOAT_DAYS} days total float.'))

    # 3. Level-4 activity duration ceiling (~3 weeks).
    long_activities = [
        a for a in activities
        if not a.get('is_milestone') and (a.get('original_duration_days') or 0) > LEVEL4_MAX_ACTIVITY_DURATION_DAYS
    ]
    if long_activities:
        issues.append(_issue(
            'level4_duration', 'warning',
            f'{len(long_activities)} activities exceed the {LEVEL4_MAX_ACTIVITY_DURATION_DAYS}-working-day '
            'Level-4 activity ceiling and should be broken down further.',
        ))
    else:
        issues.append(_issue('level4_duration', 'pass', 'All activities respect the Level-4 duration ceiling.'))

    # 4. No Start-to-Finish links (this engine only ever generates FS links).
    sf_links = [
        a for a in activities for p in a.get('predecessors', []) if p.get('type') == 'SF'
    ]
    if sf_links:
        issues.append(_issue('start_to_finish_links', 'critical',
                              f'{len(sf_links)} Start-to-Finish links detected — not permitted.'))
    else:
        issues.append(_issue('start_to_finish_links', 'pass', 'No Start-to-Finish links present.'))

    # 5. Missing predecessors (every activity except the very first milestone
    #    should link to something).
    missing_pred = [
        a for a in activities
        if not a.get('predecessors') and a['id'] not in {activities[0]['id']}
    ]
    if missing_pred:
        issues.append(_issue(
            'missing_predecessors', 'warning',
            f'{len(missing_pred)} activities have no predecessor link.',
        ))
    else:
        issues.append(_issue('missing_predecessors', 'pass', 'All activities (except the project start) have a predecessor.'))

    # 6. MDR / SOW deliverable coverage — every default deliverable for every
    #    discipline present in the source documents must appear in the EDDR.
    eddr_names = {(row['discipline'], row['deliverable_name']) for row in eddr}
    missing_deliverables = []
    for disc_code, deliverables in DISCIPLINE_DEFAULT_DELIVERABLES.items():
        mentioned = set(intelligence.get('disciplines', {}).get(disc_code, {}).get('mentioned_in_source', []))
        for deliverable in mentioned:
            if (disc_code, deliverable) not in eddr_names:
                missing_deliverables.append(f'{disc_code}: {deliverable}')
    if missing_deliverables:
        issues.append(_issue(
            'deliverable_coverage', 'critical',
            f'{len(missing_deliverables)} deliverables mentioned in source documents are missing from the schedule: '
            + ', '.join(missing_deliverables[:10]),
        ))
    else:
        issues.append(_issue('deliverable_coverage', 'pass', 'All source-document-referenced deliverables are scheduled.'))

    # 7. Milestones present.
    milestone_count = sum(1 for a in activities if a.get('is_milestone'))
    if milestone_count == 0:
        issues.append(_issue('milestones_present', 'critical', 'No milestones were generated.'))
    else:
        issues.append(_issue('milestones_present', 'pass', f'{milestone_count} milestones generated.'))

    return issues
