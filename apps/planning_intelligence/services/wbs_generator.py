"""
WBS Generator (MODULE 4) — builds the Level-2/3 work breakdown structure
from the Planning Knowledge Base + detected disciplines/deliverables.

WBS code convention: 1 = project root, 1.x = discipline, 1.x.y = deliverable
group. Kept intentionally shallow (3 levels) for the MVP — deep enough for
Primavera-style rollups without over-engineering a generic tree editor.
"""
from __future__ import annotations

from ..config import (
    DISCIPLINE_NAME_BY_CODE,
    ENGINEERING_DISCIPLINE_ORDER,
)

# Level-2 WBS ordering: non-engineering phases first, then engineering
# disciplines (in execution order), then closing phases.
_WBS_LEVEL2_ORDER = (
    ['pm', 'pc', 'survey', 'hse'] + ENGINEERING_DISCIPLINE_ORDER +
    ['3d_model', 'constructability', 'tiein', 'procurement', 'pdr', 'epc', 'closeout']
)


def build_wbs(project, intelligence: dict) -> list:
    """Returns a flat list of WBS node dicts (project code = '1')."""
    nodes = [{
        'code': '1',
        'name': project.name or 'Project',
        'level': 0,
        'parent_code': None,
    }]

    disciplines_intel = intelligence.get('disciplines', {})

    seq = 1
    for disc_code in _WBS_LEVEL2_ORDER:
        disc_info = disciplines_intel.get(disc_code, {}) if isinstance(disciplines_intel, dict) else {}
        # Skip disciplines the planner (or the BYOK scope pass) has flagged
        # out of scope — they contribute neither a WBS branch nor schedule.
        if disc_info.get('in_scope') is False:
            continue
        disc_name = DISCIPLINE_NAME_BY_CODE.get(disc_code, disc_code.title())
        l2_code = f'1.{seq}'
        nodes.append({
            'code': l2_code,
            'name': disc_name,
            'level': 1,
            'parent_code': '1',
            'discipline': disc_code,
        })

        deliverables = disc_info.get('deliverables', [])
        for d_index, deliverable in enumerate(deliverables, start=1):
            l3_code = f'{l2_code}.{d_index}'
            nodes.append({
                'code': l3_code,
                'name': deliverable,
                'level': 2,
                'parent_code': l2_code,
                'discipline': disc_code,
            })
        seq += 1

    return nodes
