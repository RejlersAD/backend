"""
Activity Generator (MODULE 5 + MODULE 6) — the core scheduling engine.

CRITICAL RULE (per spec): a deliverable is NEVER a single activity. Every
deliverable/HSE-study/survey step is expanded into its full review-cycle
workflow (Prepare -> IFR -> Company Review -> IFA -> Company Approval ->
Issue) using the soft-coded templates in config.py.

This is a simplified-but-coherent CPM-style scheduler: each logical group
(survey, one HSE study, one discipline's deliverable list) is built as a
single linear working-day chain. Cross-group logic links model real
discipline dependencies (e.g. Piping/Mechanical/Electrical/Instrumentation
all wait on the first Process deliverable). Total float is derived by
comparing each chain's finish date against the overall project finish date —
chains that determine the project finish date are flagged critical (float
= 0); all others carry the working-day gap as their float.
"""
from __future__ import annotations

import datetime

from ..config import (
    DEFAULT_CALENDAR, DEFAULT_REVIEW_CYCLE_DAYS, DELIVERABLE_WORKFLOW_STEPS,
    DISCIPLINE_PREFIX_BY_CODE, DISCIPLINE_RESPONSIBLE_ROLE, ENGINEERING_DISCIPLINE_ORDER,
    HSE_STUDY_WORKFLOW_STEPS, MAX_ALLOWED_LAG_DAYS, MILESTONE_TEMPLATE, SURVEY_WORKFLOW_STEPS,
)
from .calendar_utils import add_working_days, working_days_between

_MODEL_REVIEW_OFFSETS_WEEKS = (8, 16, 24)  # soft-coded 30/60/90% model review spacing


def _build_wbs_lookup(wbs: list) -> tuple[dict, dict]:
    """Returns (discipline_wbs, deliverable_wbs) so activities can be routed
    to the correct WBS branch that WBS Builder actually generated. Falls back
    to project root '1' when a discipline node wasn't emitted."""
    discipline_wbs: dict[str, str] = {}
    deliverable_wbs: dict[tuple[str, str], str] = {}
    for node in wbs or []:
        disc = node.get('discipline')
        if not disc:
            continue
        if node.get('level') == 1:
            discipline_wbs[disc] = node['code']
        elif node.get('level') == 2:
            deliverable_wbs[(disc, (node.get('name') or '').strip().lower())] = node['code']
    return discipline_wbs, deliverable_wbs


def _resolve_deliverable_wbs(disc_wbs_map: dict, deliverable_wbs_map: dict,
                              disc_code: str, deliverable_name: str) -> str:
    key = (disc_code, (deliverable_name or '').strip().lower())
    return (deliverable_wbs_map.get(key)
            or disc_wbs_map.get(disc_code)
            or '1')


class _IdCounter:
    """Activity-ID allocator: PR-100, PR-110, PR-120 ... (+10 per step)."""
    def __init__(self):
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        n = self._counters.get(prefix, 100)
        self._counters[prefix] = n + 10
        return f'{prefix}-{n}'


def _merged_review_days(project) -> dict:
    days = dict(DEFAULT_REVIEW_CYCLE_DAYS)
    days.update(getattr(project, 'review_cycle_overrides', None) or {})
    return days


def _merged_calendar(project) -> dict:
    cal = dict(DEFAULT_CALENDAR)
    cal.update(getattr(project, 'calendar_overrides', None) or {})
    return cal


def _step_duration(step: dict, review_days: dict) -> int:
    if step.get('cycle_key'):
        return int(review_days.get(step['cycle_key'], 5))
    return int(step.get('duration', 1))


def _build_chain(*, ids: _IdCounter, prefix: str, wbs_code: str, discipline: str,
                  role: str, steps: list, name: str, start_date, calendar, review_days,
                  is_milestone_flags=True) -> list:
    """Builds one linear working-day chain of activities from a step template."""
    chain = []
    cursor = start_date
    prev_id = None
    for step in steps:
        duration = _step_duration(step, review_days)
        activity_id = ids.next(prefix)
        finish = add_working_days(cursor, duration, calendar['working_days_per_week'])
        chain.append({
            'id': activity_id,
            'wbs_code': wbs_code,
            'name': step['suffix'].format(name=name),
            'discipline': discipline,
            'deliverable': name,
            'responsible_role': role,
            'original_duration_days': duration,
            'start_date': cursor.isoformat(),
            'finish_date': finish.isoformat(),
            'predecessors': [{'id': prev_id, 'type': 'FS'}] if prev_id else [],
            'is_milestone': bool(step.get('milestone')) if is_milestone_flags else False,
            'total_float_days': None,   # filled in during float pass
            'is_critical': False,
        })
        prev_id = activity_id
        cursor = finish
    return chain


def _milestone_activity(ids: _IdCounter, name: str, date_: datetime.date,
                         predecessor_id: str | None = None, wbs_code: str = '1',
                         discipline: str = 'pm', role: str = 'Project Manager') -> dict:
    return {
        'id': ids.next('MS'),
        'wbs_code': wbs_code,
        'name': name,
        'discipline': discipline,
        'deliverable': None,
        'responsible_role': role,
        'original_duration_days': 0,
        'start_date': date_.isoformat(),
        'finish_date': date_.isoformat(),
        'predecessors': [{'id': predecessor_id, 'type': 'FS'}] if predecessor_id else [],
        'is_milestone': True,
        'total_float_days': None,
        'is_critical': False,
    }


def build_activities(project, wbs: list, intelligence: dict) -> dict:
    """
    Returns {'activities': [...], 'logic_matrix': [...]} — logic_matrix is a
    flattened predecessor/successor table (Activity ID / Predecessor ID / Type)
    convenient for export and validation.
    """
    calendar = _merged_calendar(project)
    review_days = _merged_review_days(project)
    effective_date = project.effective_date or datetime.date.today()
    ids = _IdCounter()
    activities: list[dict] = []

    # Resolve WBS codes from what WBS Builder actually generated so
    # Primavera .xer import lands every task under its discipline folder
    # instead of dumping them all at the project root.
    disc_wbs, deliverable_wbs = _build_wbs_lookup(wbs)
    pm_wbs = disc_wbs.get('pm', '1')
    survey_wbs = disc_wbs.get('survey', '1')
    hse_wbs = disc_wbs.get('hse', '1')

    # ── Milestones: project start ──────────────────────────────────────────
    m_award = _milestone_activity(ids, MILESTONE_TEMPLATE[0], effective_date, wbs_code=pm_wbs)
    m_kickoff_date = add_working_days(effective_date, 1, calendar['working_days_per_week'])
    m_kickoff = _milestone_activity(ids, MILESTONE_TEMPLATE[1], m_kickoff_date, m_award['id'], wbs_code=pm_wbs)
    m_mobilize_date = add_working_days(m_kickoff_date, 3, calendar['working_days_per_week'])
    m_mobilize = _milestone_activity(ids, MILESTONE_TEMPLATE[2], m_mobilize_date, m_kickoff['id'], wbs_code=pm_wbs)
    activities += [m_award, m_kickoff, m_mobilize]

    # ── Survey / Studies chain ──────────────────────────────────────────────
    survey_chain = _build_chain(
        ids=ids, prefix=DISCIPLINE_PREFIX_BY_CODE['survey'], wbs_code=survey_wbs,
        discipline='survey', role='Planning Engineer', steps=SURVEY_WORKFLOW_STEPS,
        name='Site Survey', start_date=m_mobilize_date, calendar=calendar, review_days=review_days,
    )
    survey_chain[0]['predecessors'] = [{'id': m_mobilize['id'], 'type': 'FS'}]
    activities += survey_chain
    survey_finish = datetime.date.fromisoformat(survey_chain[-1]['finish_date'])

    m_data_collect = _milestone_activity(ids, MILESTONE_TEMPLATE[3], survey_finish,
                                          survey_chain[-1]['id'], wbs_code=survey_wbs,
                                          discipline='survey', role='Planning Engineer')
    m_site_survey = _milestone_activity(ids, MILESTONE_TEMPLATE[4], survey_finish,
                                         survey_chain[-1]['id'], wbs_code=survey_wbs,
                                         discipline='survey', role='Planning Engineer')
    activities += [m_data_collect, m_site_survey]

    # ── HSE study chains (run in parallel, all start after survey) ─────────
    disciplines_intel = intelligence.get('disciplines', {})
    hse_in_scope = disciplines_intel.get('hse', {}).get('in_scope', True) is not False
    hse_studies = (intelligence.get('hse_studies') or []) if hse_in_scope else []
    hse_finish_dates = []
    for study_name in hse_studies:
        study_wbs = _resolve_deliverable_wbs(disc_wbs, deliverable_wbs, 'hse', study_name)
        hse_chain = _build_chain(
            ids=ids, prefix=DISCIPLINE_PREFIX_BY_CODE['hse'], wbs_code=study_wbs,
            discipline='hse', role=DISCIPLINE_RESPONSIBLE_ROLE['hse'], steps=HSE_STUDY_WORKFLOW_STEPS,
            name=study_name, start_date=survey_finish, calendar=calendar, review_days=review_days,
        )
        hse_chain[0]['predecessors'] = [{'id': survey_chain[-1]['id'], 'type': 'FS'}]
        activities += hse_chain
        hse_finish_dates.append(datetime.date.fromisoformat(hse_chain[-1]['finish_date']))

    hse_overall_finish = max(hse_finish_dates) if hse_finish_dates else survey_finish
    m_hse_done = _milestone_activity(ids, MILESTONE_TEMPLATE[7], hse_overall_finish,
                                      wbs_code=hse_wbs, discipline='hse',
                                      role=DISCIPLINE_RESPONSIBLE_ROLE.get('hse', 'HSE Engineer'))
    activities.append(m_hse_done)

    # ── Basis of Design milestone — gate for all engineering disciplines ───
    bod_start = survey_finish
    m_bod = _milestone_activity(ids, MILESTONE_TEMPLATE[5], bod_start, survey_chain[-1]['id'],
                                 wbs_code=pm_wbs)
    activities.append(m_bod)

    # ── Engineering discipline chains ───────────────────────────────────────
    discipline_chain_finish: dict[str, datetime.date] = {}
    discipline_chain_last_activities: dict[str, list[str]] = {}
    process_first_deliverable_id = None
    process_first_deliverable_finish = None

    for disc_code in ENGINEERING_DISCIPLINE_ORDER:
        disc_info = disciplines_intel.get(disc_code, {})
        if disc_info.get('in_scope') is False:
            continue
        deliverables = disc_info.get('deliverables', [])
        if not deliverables:
            continue
        prefix = DISCIPLINE_PREFIX_BY_CODE[disc_code]
        role = DISCIPLINE_RESPONSIBLE_ROLE.get(disc_code, 'Engineer')

        chain_start = bod_start
        predecessor_override = m_bod['id']
        if disc_code != 'process' and process_first_deliverable_id:
            # Discipline logic: piping/mechanical/civil/electrical/instrumentation
            # wait on the first Process deliverable (Process Design Basis).
            chain_start = process_first_deliverable_finish
            predecessor_override = process_first_deliverable_id

        # Deliverables within a discipline run IN PARALLEL (different engineers
        # own different documents) — only the discipline's first deliverable
        # gates downstream disciplines. This keeps the overall duration
        # realistic (~10-month FEED) instead of serializing every document.
        finish_dates = []
        last_activity_ids = []
        for index, deliverable in enumerate(deliverables):
            del_wbs = _resolve_deliverable_wbs(disc_wbs, deliverable_wbs, disc_code, deliverable)
            deliverable_chain = _build_chain(
                ids=ids, prefix=prefix, wbs_code=del_wbs, discipline=disc_code, role=role,
                steps=DELIVERABLE_WORKFLOW_STEPS, name=deliverable, start_date=chain_start,
                calendar=calendar, review_days=review_days,
            )
            deliverable_chain[0]['predecessors'] = [{'id': predecessor_override, 'type': 'FS'}]
            activities += deliverable_chain
            finish_dates.append(datetime.date.fromisoformat(deliverable_chain[-1]['finish_date']))
            last_activity_ids.append(deliverable_chain[-1]['id'])

            if disc_code == 'process' and index == 0:
                process_first_deliverable_id = deliverable_chain[-1]['id']
                process_first_deliverable_finish = finish_dates[-1]

        discipline_chain_finish[disc_code] = max(finish_dates)
        discipline_chain_last_activities[disc_code] = last_activity_ids

    engineering_start = bod_start
    engineering_finish = max(discipline_chain_finish.values()) if discipline_chain_finish else bod_start

    # ── 3D Model review milestones (soft-coded offsets from engineering start) ──
    model_wbs = disc_wbs.get('3d_model', pm_wbs)
    for pct, weeks in zip((30, 60, 90), _MODEL_REVIEW_OFFSETS_WEEKS):
        review_date = add_working_days(engineering_start, weeks * calendar['working_days_per_week'],
                                        calendar['working_days_per_week'])
        review_date = min(review_date, engineering_finish)
        activities.append(_milestone_activity(ids, f'{pct}% Model Review', review_date,
                                               wbs_code=model_wbs, discipline='3d_model',
                                               role='Piping Lead'))

    # ── PDR / EPC Tender / Final Dossier / Closeout ─────────────────────────
    pdr_wbs = disc_wbs.get('pdr', '1')
    pdr_chain = _build_chain(
        ids=ids, prefix=DISCIPLINE_PREFIX_BY_CODE['pdr'], wbs_code=pdr_wbs,
        discipline='pdr', role=DISCIPLINE_RESPONSIBLE_ROLE['pdr'],
        steps=DELIVERABLE_WORKFLOW_STEPS, name='Project Definition Report (PDR)',
        start_date=engineering_finish, calendar=calendar, review_days=review_days,
    )
    pdr_chain[0]['predecessors'] = [
        {'id': aid, 'type': 'FS'}
        for last_ids in discipline_chain_last_activities.values() for aid in last_ids
    ]
    activities += pdr_chain
    pdr_finish = datetime.date.fromisoformat(pdr_chain[-1]['finish_date'])
    activities.append(_milestone_activity(ids, MILESTONE_TEMPLATE[11], pdr_finish,
                                           pdr_chain[-1]['id'], wbs_code=pdr_wbs,
                                           discipline='pdr',
                                           role=DISCIPLINE_RESPONSIBLE_ROLE.get('pdr', 'Project Manager')))

    epc_wbs = disc_wbs.get('epc', '1')
    epc_chain = _build_chain(
        ids=ids, prefix=DISCIPLINE_PREFIX_BY_CODE['epc'], wbs_code=epc_wbs,
        discipline='epc', role=DISCIPLINE_RESPONSIBLE_ROLE['epc'],
        steps=DELIVERABLE_WORKFLOW_STEPS, name='EPC Tender Package',
        start_date=pdr_finish, calendar=calendar, review_days=review_days,
    )
    epc_chain[0]['predecessors'] = [{'id': pdr_chain[-1]['id'], 'type': 'FS'}]
    activities += epc_chain
    epc_finish = datetime.date.fromisoformat(epc_chain[-1]['finish_date'])
    activities.append(_milestone_activity(ids, MILESTONE_TEMPLATE[12], epc_finish,
                                           epc_chain[-1]['id'], wbs_code=epc_wbs,
                                           discipline='epc',
                                           role=DISCIPLINE_RESPONSIBLE_ROLE.get('epc', 'Project Manager')))
    activities.append(_milestone_activity(ids, MILESTONE_TEMPLATE[13], epc_finish,
                                           epc_chain[-1]['id'], wbs_code=epc_wbs,
                                           discipline='epc',
                                           role=DISCIPLINE_RESPONSIBLE_ROLE.get('epc', 'Project Manager')))

    closeout_wbs = disc_wbs.get('closeout', pm_wbs)
    closeout_finish = add_working_days(epc_finish, 5, calendar['working_days_per_week'])
    activities.append(_milestone_activity(ids, MILESTONE_TEMPLATE[14], closeout_finish,
                                           epc_chain[-1]['id'], wbs_code=closeout_wbs,
                                           discipline='closeout', role='Project Manager'))

    # ── Total float pass ────────────────────────────────────────────────────
    project_finish = max(
        datetime.date.fromisoformat(a['finish_date']) for a in activities
    )
    _assign_float(activities, project_finish, calendar)

    logic_matrix = [
        {'activity_id': a['id'], 'predecessor_id': p['id'], 'type': p['type']}
        for a in activities for p in a['predecessors'] if p.get('id')
    ]

    return {'activities': activities, 'logic_matrix': logic_matrix, 'project_finish_date': project_finish.isoformat()}


def _assign_float(activities: list, project_finish: datetime.date, calendar: dict) -> None:
    """Group activities into their linear chains (by shared discipline+deliverable
    lineage is overkill for MVP) — instead we compute float per activity as the
    gap between the project finish and that activity's own chain finish, found
    by walking forward via the successor graph. Simplified: float = working
    days between this activity's finish and the latest finish date reachable
    from it; chains ending exactly at project_finish are critical (float=0)."""
    by_id = {a['id']: a for a in activities}
    successors: dict[str, list[str]] = {a['id']: [] for a in activities}
    for a in activities:
        for p in a['predecessors']:
            if p.get('id') and p['id'] in successors:
                successors[p['id']].append(a['id'])

    memo: dict[str, datetime.date] = {}

    def latest_reachable_finish(activity_id: str) -> datetime.date:
        if activity_id in memo:
            return memo[activity_id]
        own_finish = datetime.date.fromisoformat(by_id[activity_id]['finish_date'])
        best = own_finish
        for succ_id in successors.get(activity_id, []):
            best = max(best, latest_reachable_finish(succ_id))
        memo[activity_id] = best
        return best

    for a in activities:
        reach = latest_reachable_finish(a['id'])
        gap = working_days_between(reach, project_finish)
        a['total_float_days'] = gap
        a['is_critical'] = gap == 0
