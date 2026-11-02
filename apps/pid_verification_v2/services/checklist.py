"""
Checklist Loader — P&ID Verification V2
=======================================

The client checklist is one of the three compulsory inputs to a check run
(drawings, legend sheets, checklist — decision 29 Oct 2026; registers are
no longer inputs).

DEFAULT CHECKLIST: PRO-001 Rev A — 104 points
---------------------------------------------

No PRO-001 point definitions exist anywhere in this repository yet
(searched 29 Oct 2026: no 'PRO-001', no checklist xlsx/json). Until the
actual 104-point definition is supplied, this module provides an honest
STUB: the checklist is reported as CONFIGURED (PRO-001 Rev A, 104 points)
but every point evaluates to 'not_checked' — no fake pass/fail results
are ever produced.

Resolution order for a project:
  1. Uploaded checklist: latest COMPLETED PIDVReferenceData with
     data_type='checklist' (parsed via parse_reference_data_task) — its
     parsed rows become the point list. Point evaluation is still
     'not_checked' until per-point evaluators are implemented.
  2. Default stub: PRO-001 Rev A, 104 points, all 'not_checked'.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Default checklist identity (soft-coded) ──────────────────────────────
DEFAULT_CHECKLIST_CODE = 'PRO-001'
DEFAULT_CHECKLIST_REVISION = 'A'
DEFAULT_CHECKLIST_POINT_COUNT = 104

# Honest evaluation status for every point until per-point evaluators exist.
NOT_CHECKED = 'not_checked'


def _stub_points(count: int) -> List[Dict[str, Any]]:
    """Placeholder point entries — explicitly marked, always 'not_checked'."""
    return [
        {
            'point_no': i,
            'text': '',               # no point text known yet
            'result': NOT_CHECKED,    # honest: never a fabricated pass/fail
            'stub': True,
        }
        for i in range(1, count + 1)
    ]


def get_project_checklist(project) -> Dict[str, Any]:
    """Resolve the checklist configuration for a project.

    Returns a dict:
      {
        'code':        'PRO-001',
        'revision':    'A',
        'configured':  True,
        'source':      'uploaded:<reference_id>' | 'default_stub',
        'point_count': int,
        'points':      [{point_no, text, result, ...}],
        'evaluation':  'not_checked',   # overall — no point evaluators yet
      }
    """
    if project is not None:
        try:
            from apps.pid_verification_v2.models import PIDVReferenceData
            ref = (
                PIDVReferenceData.objects
                .filter(
                    project=project,
                    data_type=PIDVReferenceData.DataType.CHECKLIST,
                    status=PIDVReferenceData.Status.COMPLETED,
                )
                .exclude(parsed_data__isnull=True)
                .order_by('-created_at')
                .first()
            )
        except Exception:
            logger.debug('[Checklist] Checklist query failed', exc_info=True)
            ref = None

        if ref and ref.parsed_data:
            points = [
                {
                    'point_no': i,
                    'text': str(row),
                    'result': NOT_CHECKED,
                    'stub': False,
                }
                for i, row in enumerate(ref.parsed_data, start=1)
            ]
            logger.info(
                '[Checklist] Using uploaded checklist %s (%d points) for project=%s',
                ref.file_name, len(points), getattr(project, 'project_id', None),
            )
            return {
                'code': DEFAULT_CHECKLIST_CODE,
                'revision': DEFAULT_CHECKLIST_REVISION,
                'configured': True,
                'source': f'uploaded:{ref.reference_id}',
                'point_count': len(points),
                'points': points,
                'evaluation': NOT_CHECKED,
            }

    logger.info('[Checklist] No uploaded checklist — using default stub %s Rev %s (%d points)',
                DEFAULT_CHECKLIST_CODE, DEFAULT_CHECKLIST_REVISION, DEFAULT_CHECKLIST_POINT_COUNT)
    return {
        'code': DEFAULT_CHECKLIST_CODE,
        'revision': DEFAULT_CHECKLIST_REVISION,
        'configured': True,
        'source': 'default_stub',
        'point_count': DEFAULT_CHECKLIST_POINT_COUNT,
        'points': _stub_points(DEFAULT_CHECKLIST_POINT_COUNT),
        'evaluation': NOT_CHECKED,
    }
