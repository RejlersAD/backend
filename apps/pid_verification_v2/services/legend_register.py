"""
Legend Register helpers — P&ID Verification V2
==============================================

Read access to an uploaded Excel legend register (PIDVReferenceData,
data_type='legend_pid', parsed multi-sheet by parse_reference_data_task —
e.g. Borouge_PID_Legend_Register.xlsx with sheets Codes / ID Letters /
Notes / Documents / Drawing Register).

Used by: legend lookups (legend_bridge), title-block verification, the
in_register flag on connectors, and the Borouge project rules.

All functions return empty structures when no register is uploaded.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def get_register_sheets(project) -> Dict[str, list]:
    """{sheet_name: [row dicts]} of the project's latest COMPLETED legend
    register, or {} when none / old single-sheet shape."""
    if project is None:
        return {}
    try:
        from apps.pid_verification_v2.models import PIDVReferenceData
        ref = (
            PIDVReferenceData.objects
            .filter(
                project=project,
                data_type=PIDVReferenceData.DataType.LEGEND_PID,
                status=PIDVReferenceData.Status.COMPLETED,
            )
            .exclude(parsed_data__isnull=True)
            .order_by('-created_at')
            .first()
        )
    except Exception:
        logger.debug('[LegendRegister] query failed', exc_info=True)
        return {}
    if not ref or not ref.parsed_data:
        return {}
    if isinstance(ref.parsed_data, dict) and 'sheets' in ref.parsed_data:
        return ref.parsed_data['sheets']
    return {}


def _find_col(row: dict, *hints) -> Optional[str]:
    for key in row:
        kl = str(key).lower()
        if any(h in kl for h in hints):
            return key
    return None


def get_register_codes(project) -> Dict[str, Dict[str, str]]:
    """From the 'Codes' sheet: {category: {code: description}}."""
    sheets = get_register_sheets(project)
    rows = sheets.get('Codes') or []
    out: Dict[str, Dict[str, str]] = {}
    if not rows:
        return out
    cat_col = _find_col(rows[0], 'categor')
    code_col = _find_col(rows[0], 'code')
    desc_col = _find_col(rows[0], 'descri')
    if not (code_col and desc_col):
        return out
    for row in rows:
        code = str(row.get(code_col, '')).strip()
        desc = str(row.get(desc_col, '')).strip()
        cat = str(row.get(cat_col, '')).strip() if cat_col else 'General'
        if not code or not desc:
            continue
        out.setdefault(cat or 'General', {})[code] = desc
    return out


def code_in_register(project, code: str) -> bool:
    """True when `code` appears in the register's Codes sheet (any category)."""
    code_u = (code or '').strip().upper()
    if not code_u:
        return False
    for codes in get_register_codes(project).values():
        if code_u in {c.upper() for c in codes}:
            return True
    return False


def get_register_documents(project) -> Dict[str, dict]:
    """From the 'Documents' sheet (P&ID register):
    {borouge_doc_no: {'title', 'latest_issue', 'latest_date', 'status'}}."""
    sheets = get_register_sheets(project)
    rows = sheets.get('Documents') or []
    out: Dict[str, dict] = {}
    if not rows:
        return out
    no_col = _find_col(rows[0], 'borouge doc')
    title_col = _find_col(rows[0], 'document title', 'title')
    issue_col = _find_col(rows[0], 'latest issue')
    date_col = _find_col(rows[0], 'latest date')
    status_col = _find_col(rows[0], 'latest status', 'status')
    if not no_col:
        return out
    for row in rows:
        no = str(row.get(no_col, '')).strip()
        if not re.match(r'^\d{2}-[A-Z]{2}-\d{3}-\d{4,6}$', no):
            continue
        out[no] = {
            'title': str(row.get(title_col, '')).strip() if title_col else '',
            'latest_issue': str(row.get(issue_col, '')).strip() if issue_col else '',
            'latest_date': str(row.get(date_col, '')).strip() if date_col else '',
            'status': str(row.get(status_col, '')).strip() if status_col else '',
        }
    return out


def get_register_legend_drawings(project) -> set:
    """Drawing numbers of the legend sheet set ('Drawing Register' sheet)."""
    sheets = get_register_sheets(project)
    rows = sheets.get('Drawing Register') or []
    out = set()
    if not rows:
        return out
    no_col = _find_col(rows[0], 'borouge drawing', 'drawing')
    if not no_col:
        return out
    for row in rows:
        no = str(row.get(no_col, '')).strip()
        no = re.sub(r'-P\d+$', '', no)
        if re.match(r'^(?:\d{2}|E\d)-[A-Z]{2}-\d{3}-\d{4,6}$', no):
            out.add(no)
    return out


def code_known_anywhere(project, code: str) -> Optional[bool]:
    """Loose membership check across the WHOLE register workbook (Codes
    tables first, then a word-boundary search of every sheet) — used for
    vent/utility codes (e.g. AP7E lives in legend detail sheets, not the
    Codes table; AP5ET is genuinely absent). Returns None when no
    register is available."""
    import json as _json
    code_u = (code or '').strip()
    if not code_u:
        return None
    sheets = get_register_sheets(project)
    if not sheets:
        return None
    for table in get_register_codes(project).values():
        if code_u.upper() in {c.upper() for c in table}:
            return True
    blob = _json.dumps(sheets)
    return bool(re.search(r'(?<![A-Za-z0-9])' + re.escape(code_u) + r'(?![A-Za-z0-9])', blob))


def drawing_in_register(project, drawing_no: str) -> Optional[bool]:
    """True/False when a register is available, None when no register."""
    no = re.sub(r'-P\d+$', '', (drawing_no or '').strip())
    if not no:
        return None
    docs = get_register_documents(project)
    legend = get_register_legend_drawings(project)
    if not docs and not legend:
        return None
    return no in docs or no in legend
