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
import re

from ..config import (
    DEFAULT_CALENDAR, DEFAULT_REVIEW_CYCLE_DAYS, DELIVERABLE_WORKFLOW_STEPS,
    DISCIPLINE_PREFIX_BY_CODE, DISCIPLINE_RESPONSIBLE_ROLE, ENGINEERING_DISCIPLINE_ORDER,
    HSE_STUDY_WORKFLOW_STEPS, MAX_ALLOWED_LAG_DAYS, MILESTONE_TEMPLATE, SURVEY_WORKFLOW_STEPS,
)
from .calendar_utils import add_working_days, working_days_between

_MODEL_REVIEW_OFFSETS_WEEKS = (8, 16, 24)  # soft-coded 30/60/90% model review spacing

# ── Deliverable categorization for smart sequencing ────────────────────────
DELIVERABLE_CATEGORIES = {
    'design_basis': ['design basis', 'basis of design', 'philosophy', 'criteria'],
    'studies': ['study', 'analysis', 'calculation', 'assessment', 'report', 'evaluation'],
    'diagrams': ['diagram', 'bfd', 'pfd', 'p&id', 'sld', 'cause & effect', 'c&e'],
    'drawings': ['drawing', 'layout', 'arrangement', 'plot plan', 'ga'],
    'lists': ['list', 'schedule', 'index', 'register', 'mto', 'bill of materials'],
    'specifications': ['specification', 'spec', 'standard'],
    'datasheets': ['datasheet', 'data sheet'],
}

CATEGORY_SEQUENCE_ORDER = [
    'design_basis',      # Priority 1: Must come first (drivers)
    'specifications',    # Priority 2: Standards and specs
    'studies',          # Priority 3: Engineering studies
    'diagrams',         # Priority 4: Process/electrical diagrams
    'datasheets',       # Priority 5: Equipment datasheets
    'drawings',         # Priority 6: Detailed drawings
    'lists',            # Priority 7: Lists and schedules (last)
]


def _categorize_deliverable(deliverable_name: str) -> tuple[str, int]:
    """Returns (category, priority) for smart sequencing. Lower priority = earlier."""
    name_lower = deliverable_name.lower()
    for category, keywords in DELIVERABLE_CATEGORIES.items():
        if any(kw in name_lower for kw in keywords):
            try:
                priority = CATEGORY_SEQUENCE_ORDER.index(category)
            except ValueError:
                priority = 99
            return category, priority
    return 'other', 50  # default middle priority


def _determine_relationship_type(pred_category: str, succ_category: str, 
                                  pred_name: str, succ_name: str) -> tuple[str, int]:
    """
    Determines relationship type and lag days between two deliverables.
    Returns (relationship_type, lag_days).
    
    Relationship types:
    - FS (Finish-to-Start): 90% of relationships - one must finish before next starts
    - SS (Start-to-Start): Activities can start together with lag
    - FF (Finish-to-Finish): Activities must finish together
    - SF (Start-to-Finish): Rarely used
    """
    # Design Basis → Everything else: FS with 0 lag (must finish first)
    if pred_category == 'design_basis':
        return 'FS', 0
    
    # Specifications → Studies/Diagrams: SS with 3-day lag (can start together)
    if pred_category == 'specifications' and succ_category in ['studies', 'diagrams']:
        return 'SS', 3
    
    # Studies → Diagrams: SS with 5-day lag (studies can inform diagrams as they progress)
    if pred_category == 'studies' and succ_category == 'diagrams':
        return 'SS', 5
    
    # Diagrams → Datasheets: FS with 2-day lag
    if pred_category == 'diagrams' and succ_category == 'datasheets':
        return 'FS', 2
    
    # Diagrams → Drawings: SS with 7-day lag (drawings can start while diagrams finalize)
    if pred_category == 'diagrams' and succ_category == 'drawings':
        return 'SS', 7
    
    # Datasheets → Lists: FS with 0 lag
    if pred_category == 'datasheets' and succ_category == 'lists':
        return 'FS', 0
    
    # Drawings → Lists: FF with 0 lag (lists finalize with drawings)
    if pred_category == 'drawings' and succ_category == 'lists':
        return 'FF', 0
    
    # Default: FS with 3-day lag (conservative sequencing)
    return 'FS', 3


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
                  is_milestone_flags=True, predecessors=None) -> list:
    """Builds one linear working-day chain of activities from a step template.
    
    Args:
        predecessors: List of dicts with 'id', 'type', and optional 'lag_days' for the FIRST activity in chain
    """
    if predecessors is None:
        predecessors = []
        
    chain = []
    cursor = start_date
    prev_id = None
    for step in steps:
        duration = _step_duration(step, review_days)
        activity_id = ids.next(prefix)
        finish = add_working_days(cursor, duration, calendar['working_days_per_week'])
        
        # First activity gets external predecessors, rest link internally with FS
        if prev_id is None:
            activity_predecessors = predecessors
        else:
            activity_predecessors = [{'id': prev_id, 'type': 'FS', 'lag_days': 0}]
        
        chain.append({
            'id': activity_id,
            'wbs_code': wbs_code,
            'name': step['suffix'].format(name=name),
            'discipline': discipline,
            'deliverable': name,
            'responsible_role': role,
            'workflow_status': step.get('status', ''),  # Workflow stage marker (Start, IFR, IFA, Final Issue)
            'original_duration_days': duration,
            'start_date': cursor.isoformat(),
            'finish_date': finish.isoformat(),
            'predecessors': activity_predecessors,
            'is_milestone': bool(step.get('milestone')) if is_milestone_flags else False,
            'total_float_days': None,   # filled in during float pass
            'is_critical': False,
        })
        prev_id = activity_id
        cursor = finish
    return chain


def _milestone_activity(ids: _IdCounter, name: str, date_: datetime.date,
                         predecessor_id: str | None = None, wbs_code: str = '1',
                         discipline: str = 'pm', role: str = 'Project Manager',
                         predecessors: list = None) -> dict:
    """Create a milestone activity. Can accept either predecessor_id or predecessors list."""
    if predecessors is None:
        if predecessor_id:
            predecessors = [{'id': predecessor_id, 'type': 'FS', 'lag_days': 0}]
        else:
            predecessors = []
            
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
        'predecessors': predecessors,
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
        predecessors=[{'id': m_mobilize['id'], 'type': 'FS', 'lag_days': 0}]
    )
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
            predecessors=[{'id': survey_chain[-1]['id'], 'type': 'FS', 'lag_days': 0}]
        )
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

    # ── Engineering discipline chains with SMART SEQUENCING ────────────────
    discipline_chain_finish: dict[str, datetime.date] = {}
    discipline_chain_last_activities: dict[str, list[str]] = {}
    discipline_deliverable_chains: dict[str, list[tuple[str, list[dict]]]] = {}  # discipline -> [(deliverable_name, chain)]
    
    process_first_deliverable_id = None
    process_first_deliverable_finish = None
    process_design_basis_id = None  # Special tracking for Process Design Basis

    for disc_code in ENGINEERING_DISCIPLINE_ORDER:
        disc_info = disciplines_intel.get(disc_code, {})
        if disc_info.get('in_scope') is False:
            continue
        deliverables = disc_info.get('deliverables', [])
        excluded = set(disc_info.get('excluded_deliverables', []))
        deliverables = [d for d in deliverables if d not in excluded]
        if not deliverables:
            continue
            
        prefix = DISCIPLINE_PREFIX_BY_CODE[disc_code]
        role = DISCIPLINE_RESPONSIBLE_ROLE.get(disc_code, 'Engineer')

        # ── STEP 1: Categorize and sort deliverables by priority ────
        deliverable_info = []
        for deliverable in deliverables:
            category, priority = _categorize_deliverable(deliverable)
            deliverable_info.append({
                'name': deliverable,
                'category': category,
                'priority': priority,
            })
        
        # Sort by priority (design basis first, lists last)
        deliverable_info.sort(key=lambda x: (x['priority'], x['name']))

        # ── STEP 2: Determine start point for this discipline ────
        chain_start = bod_start
        base_predecessor_id = m_bod['id']
        
        # Non-Process disciplines wait on Process deliverables
        if disc_code != 'process' and process_first_deliverable_id:
            chain_start = process_first_deliverable_finish
            base_predecessor_id = process_first_deliverable_id
            
            # Additional cross-discipline dependencies for realism
            if disc_code == 'piping' and process_design_basis_id:
                base_predecessor_id = process_design_basis_id  # Piping waits on Process Design Basis
            elif disc_code == 'mechanical' and process_first_deliverable_id:
                base_predecessor_id = process_first_deliverable_id
            elif disc_code == 'electrical':
                # Electrical can start with some lag after mechanical (for equipment power requirements)
                pass  # Will add SS relationship later
            elif disc_code == 'instrumentation':
                # Instrumentation needs P&IDs from Process/Piping
                pass

        # ── STEP 3: Build chains with smart predecessor relationships ────
        deliverable_chains_list = []
        finish_dates = []
        last_activity_ids = []
        prev_deliverable = None
        prev_chain = None
        
        for idx, del_info in enumerate(deliverable_info):
            deliverable = del_info['name']
            category = del_info['category']
            
            del_wbs = _resolve_deliverable_wbs(disc_wbs, deliverable_wbs, disc_code, deliverable)
            
            # Determine predecessors for this deliverable chain
            if idx == 0:
                # First deliverable links to discipline gate or upstream discipline
                predecessors = [{'id': base_predecessor_id, 'type': 'FS', 'lag_days': 0}]
                start_date = chain_start
            else:
                # Subsequent deliverables use smart relationship logic
                rel_type, lag_days = _determine_relationship_type(
                    prev_deliverable['category'],
                    category,
                    prev_deliverable['name'],
                    deliverable
                )
                
                # Calculate start date based on relationship type
                if rel_type == 'FS':
                    # Finish-to-Start: Start after previous finishes + lag
                    start_date = add_working_days(
                        datetime.date.fromisoformat(prev_chain[-1]['finish_date']),
                        lag_days,
                        calendar['working_days_per_week']
                    )
                    predecessors = [{'id': prev_chain[-1]['id'], 'type': 'FS', 'lag_days': lag_days}]
                    
                elif rel_type == 'SS':
                    # Start-to-Start: Can start while previous is in progress
                    start_date = add_working_days(
                        datetime.date.fromisoformat(prev_chain[0]['start_date']),
                        lag_days,
                        calendar['working_days_per_week']
                    )
                    predecessors = [{'id': prev_chain[0]['id'], 'type': 'SS', 'lag_days': lag_days}]
                    
                elif rel_type == 'FF':
                    # Finish-to-Finish: Should finish around same time as previous
                    # Calculate start based on own duration to align finish dates
                    prev_finish = datetime.date.fromisoformat(prev_chain[-1]['finish_date'])
                    # Estimate this deliverable's duration (sum of workflow steps)
                    est_duration = sum(_step_duration(step, review_days) for step in DELIVERABLE_WORKFLOW_STEPS)
                    start_date = add_working_days(prev_finish, -est_duration + lag_days, calendar['working_days_per_week'])
                    start_date = max(start_date, chain_start)  # Can't start before discipline starts
                    predecessors = [{'id': prev_chain[-1]['id'], 'type': 'FF', 'lag_days': lag_days}]
                    
                else:  # SF (Start-to-Finish) - rarely used
                    start_date = datetime.date.fromisoformat(prev_chain[-1]['finish_date'])
                    predecessors = [{'id': prev_chain[-1]['id'], 'type': 'SF', 'lag_days': lag_days}]
            
            # Build the deliverable workflow chain
            deliverable_chain = _build_chain(
                ids=ids, prefix=prefix, wbs_code=del_wbs, discipline=disc_code, role=role,
                steps=DELIVERABLE_WORKFLOW_STEPS, name=deliverable, start_date=start_date,
                calendar=calendar, review_days=review_days, predecessors=predecessors,
            )
            
            activities += deliverable_chain
            deliverable_chains_list.append((deliverable, deliverable_chain))
            finish_dates.append(datetime.date.fromisoformat(deliverable_chain[-1]['finish_date']))
            last_activity_ids.append(deliverable_chain[-1]['id'])
            
            # Track Process Design Basis specifically for cross-discipline dependencies
            if disc_code == 'process' and idx == 0:
                process_first_deliverable_id = deliverable_chain[-1]['id']
                process_first_deliverable_finish = finish_dates[-1]
                if 'design basis' in deliverable.lower():
                    process_design_basis_id = deliverable_chain[-1]['id']
            
            prev_deliverable = del_info
            prev_chain = deliverable_chain
        
        discipline_chain_finish[disc_code] = max(finish_dates)
        discipline_chain_last_activities[disc_code] = last_activity_ids
        discipline_deliverable_chains[disc_code] = deliverable_chains_list

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
    pdr_predecessors = [
        {'id': aid, 'type': 'FS', 'lag_days': 0}
        for last_ids in discipline_chain_last_activities.values() for aid in last_ids
    ]
    pdr_chain = _build_chain(
        ids=ids, prefix=DISCIPLINE_PREFIX_BY_CODE['pdr'], wbs_code=pdr_wbs,
        discipline='pdr', role=DISCIPLINE_RESPONSIBLE_ROLE['pdr'],
        steps=DELIVERABLE_WORKFLOW_STEPS, name='Project Definition Report (PDR)',
        start_date=engineering_finish, calendar=calendar, review_days=review_days,
        predecessors=pdr_predecessors
    )
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
        predecessors=[{'id': pdr_chain[-1]['id'], 'type': 'FS', 'lag_days': 0}]
    )
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

    # ── Build logic matrix with relationship types and lags ────────────────
    logic_matrix = [
        {
            'activity_id': a['id'], 
            'predecessor_id': p['id'], 
            'type': p.get('type', 'FS'),
            'lag_days': p.get('lag_days', 0)
        }
        for a in activities for p in a.get('predecessors', []) if p.get('id')
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
