"""
Schedule Narrative Generator (MODULE 9) — rule-based/template text generation,
optionally augmented by a per-project BYOK Claude call for prose polish only.

NOTE: This composes structured narrative text from the already-generated
schedule data. All dates/counts/figures always come from the deterministic
schedule data itself — Claude (when a project has BYOK configured) is only
ever asked to rewrite the Executive Summary prose using those exact numbers
as grounding facts; it never invents or alters any figure, and every other
section of the narrative remains fully deterministic.
"""
from __future__ import annotations

import datetime

from ..config import CLAUDE_NARRATIVE_MAX_TOKENS, DISCIPLINE_NAME_BY_CODE
from . import claude_client

_CLAUDE_SYSTEM_PROMPT = (
    'You are an engineering project-controls assistant polishing the Executive Summary '
    'paragraph of a schedule narrative for a FEED/DEFINE oil & gas project. You will be '
    'given the exact facts already computed by the scheduler. Rewrite ONLY the executive '
    'summary paragraph in clear, professional engineering-report prose. You MUST use every '
    'number/date given exactly as provided — do not invent, omit, or alter any figure. '
    'Respond with the rewritten paragraph text only — no markdown headers, no JSON, no '
    'preamble.'
)


def _augment_executive_summary(default_text: str, facts: dict, project, user) -> str:
    """Return a Claude-polished executive summary, or `default_text` unchanged
    if BYOK is not configured for this project or the call fails."""
    if claude_client.get_claude_config(project) is None:
        return default_text

    user_prompt = f'FACTS (grounding — use exactly, do not alter): {facts}\n\nDefault summary: {default_text}'
    result = claude_client.call_claude(
        project,
        system_prompt=_CLAUDE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=CLAUDE_NARRATIVE_MAX_TOKENS,
        feature='narrative_generation',
        user=user,
    )
    if result is None or not result['text'].strip():
        return default_text
    return result['text'].strip()


def build_narrative(
    project, activities: list, eddr: list, validation: list,
    milestones_finish: str | None = None, user=None,
) -> str:
    total_activities = len(activities)
    milestone_count = sum(1 for a in activities if a.get('is_milestone'))
    critical_count = sum(1 for a in activities if a.get('is_critical'))
    disciplines = sorted({a['discipline'] for a in activities if a.get('deliverable')})
    critical_issues = [i for i in validation if i['severity'] == 'critical']
    warning_issues = [i for i in validation if i['severity'] == 'warning']

    project_finish = max((a['finish_date'] for a in activities), default=None)
    effective = project.effective_date.isoformat() if project.effective_date else 'TBD'

    default_exec_summary = (
        f'This schedule covers the {project.phase or "FEED/DEFINE"} phase for {project.name}'
        f'{" (" + project.client + ")" if project.client else ""}. The plan comprises '
        f'{total_activities} activities, including {milestone_count} milestones, spanning '
        f'from the effective date {effective} to a projected finish of {project_finish or "TBD"}.'
    )
    exec_summary = _augment_executive_summary(
        default_exec_summary,
        {
            'project_name': project.name,
            'client': project.client or None,
            'phase': project.phase or 'FEED/DEFINE',
            'total_activities': total_activities,
            'milestone_count': milestone_count,
            'effective_date': effective,
            'project_finish': project_finish or 'TBD',
        },
        project, user,
    )

    lines = [
        f'# Schedule Narrative — {project.name}',
        '',
        '## 1. Executive Summary',
        exec_summary,
        '',
        '## 2. Basis of Schedule',
        f'The schedule was generated from uploaded reference documents (SOW, WBS, MDR, EDDR, '
        'Schedule Requirements) using the RADAI Planning Knowledge Base. Every deliverable is '
        'expanded into its full review-cycle workflow (Prepare -> Company Review -> Incorporate '
        'Comments -> Approval -> Issue) rather than modelled as a single activity, per the '
        'project schedule-requirements basis.',
        '',
        '## 3. Disciplines Covered',
        ', '.join(DISCIPLINE_NAME_BY_CODE.get(d, d.title()) for d in disciplines) or 'None detected',
        '',
        '## 4. Critical Path',
        f'{critical_count} activities lie on the critical path (zero total float). The critical '
        'path runs through the discipline chain(s) whose completion governs the overall project '
        'finish date; see the Activities export for the full list (is_critical = true).',
        '',
        '## 5. Engineering Document Deliverable Register (EDDR) Summary',
        f'{len(eddr)} deliverables are tracked in the EDDR, each carrying its own IFR / Company '
        'Review / IFA / Approval / Final Issue dates drawn directly from the schedule.',
        '',
        '## 6. Validation Summary',
    ]

    if critical_issues:
        lines.append(f'{len(critical_issues)} CRITICAL issue(s) require resolution before this schedule is finalized:')
        lines += [f'- {i["message"]}' for i in critical_issues]
    else:
        lines.append('No critical validation issues were found.')

    if warning_issues:
        lines.append('')
        lines.append(f'{len(warning_issues)} warning(s) to review:')
        lines += [f'- {i["message"]}' for i in warning_issues]

    lines += [
        '',
        '## 7. Assumptions & Notes',
        '- Calendar: 5-day working week, 8 hours/day (unless overridden by project settings).',
        '- Review cycle durations follow the project Schedule Requirements (or RFT Appendix-4 '
        'defaults where not specified): 5/10/5/5/2 working days.',
        '- Document intelligence and narrative text are generated by deterministic, rule-based '
        'logic' + (
            ', with the Executive Summary optionally polished by Claude (BYOK) for this project.'
            if claude_client.get_claude_config(project) is not None
            else ' — no external AI API is configured for this project.'
        ) + ' Review all generated content before issuing.',
    ]

    return '\n'.join(lines)
