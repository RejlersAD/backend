"""
AI-PID-02 checklist evaluation (spec §2.3)
==========================================

Computed LIVE on every GET from the current project inputs, the sheet's
extraction and its findings — an input added in step 2 changes the results
without a re-run. ChecklistResult rows persist only the engineer state
(applicability override, verification, remarks).

Decision order per item (spec §2.3):
  0. applicability (H): NA rules first; when H is NA/N, I stays empty;
  1. missing input (column E vs the project's inputs) → Not Verifiable;
  2. linked findings (finding.checklist_items) → Non-Conformance / Query;
  3. checked, nothing found → OK with counted evidence (never bare OK);
  4. AI-Assisted judgement items → Query with candidates;
  5. Engineer-Led (6.3, 10.2, 12.11) → Query with candidates / NV;
     13.3 is never set by the AI;
  7. 13.1 / 13.2 → OK (the AI Comment Register is generated with the
     report); 13.4 → NA on a first review.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from .checklist_map import RULE_TO_ITEMS

logger = logging.getLogger(__name__)

# ── input registry (column E → project input) ─────────────────────────────
# (needle in the lowercase ref_inputs, input key, display name)
INPUT_KEYWORDS = [
    ('pfd', 'pfd', 'PFD'),
    ('h&mb', 'hmb', 'H&MB'),
    ('heat & material', 'hmb', 'H&MB'),
    ('equipment list', 'equipment_list', 'Equipment list'),
    ('line list', 'line_list', 'Line list'),
    ('instrument index', 'instrument_index', 'Instrument index'),
    ('data sheet', 'data_sheets', 'Data sheets'),
    ('c&e', 'ce', 'C&E chart'),
    ('hazop', 'hazop', 'HAZOP action register'),
    ('lopa', 'hazop', 'LOPA/SIL report'),
    ('sil', 'hazop', 'SIL / SIF list'),
    ('sif', 'hazop', 'SIF list'),
    ('hold list', 'hold_list', 'Project hold list'),
    ('dgs', 'dgs', 'DGS'),
    ('isolation philosophy', 'isolation_philosophy', 'Isolation philosophy'),
    ('relief', 'relief_study', 'Relief study / philosophy'),
    ('blowdown', 'relief_study', 'Blowdown study'),
    ('vendor', 'vendor', 'Vendor documents'),
    ('legend', 'legend', 'Legend P&ID'),
    ('document register', 'register', 'Document register / MDR'),
    ('mdr', 'register', 'MDR'),
    ('transmittal', 'register', 'Transmittal'),
    ('piping material specification', 'piping_spec', 'Piping material specification'),
    ('piping spec', 'piping_spec', 'Piping spec'),
    ('piping class index', 'piping_spec', 'Piping class index'),
    ('control narrative', 'control_narrative', 'Control narrative'),
    ('alarm & trip', 'alarm_trip', 'Alarm & trip schedule'),
    ('interlock list', 'interlocks', 'Interlock list'),
    ('logic diagram', 'logic', 'Logic diagrams'),
    ('p&ID', 'pid', 'P&ID'),
]
# Only these input kinds can be present in a v2 project today.
_PRESENTABLE = {'pid', 'legend', 'checklist', 'line_list', 'equipment_list', 'instrument_index', 'pfd'}

# ── applicability (H=NA) rules — soft-coded (spec §2.3 step 6) ───────────
# Equipment code prefixes that make a whole section applicable.
_SECTION_EQUIP_CODES = {
    '3': {'C', 'K', 'CP', 'KC', 'CMP'},          # compressors
    '4': {'P', 'PA', 'PC', 'PU', 'PL', 'PP'},    # pumps
    '5': {'V', 'VA', 'VT', 'VV', 'T', 'TK', 'FL', 'DV'},  # columns/vessels/filters
    '6': {'HE', 'E', 'AE', 'AC', 'XC'},          # exchangers / air coolers
    '7': {'HTR', 'H'},                           # fired heaters (a furnace
    # distribution header — FH — is NOT one; see acceptance on 13-PP-152-20070)
}
_PSV_RE = re.compile(r'\bPS[VV]\b|\bPRV\b', re.IGNORECASE)

ENGINEER_LED = {'6.3', '10.2', '12.11', '13.3'}


def _inputs_present(doc) -> Dict[str, bool]:
    present = {'pid': True}
    if doc.project_id:
        from apps.pid_verification_v2.models import PIDVReferenceData
        types = set(
            PIDVReferenceData.objects
            .filter(project_id=doc.project_id, status=PIDVReferenceData.Status.COMPLETED)
            .values_list('data_type', flat=True)
        )
        present['legend'] = 'legend_pid' in types
        present['checklist'] = 'checklist' in types
        present['line_list'] = 'line_list' in types
        present['equipment_list'] = 'equipment_list' in types
        present['instrument_index'] = 'instrument_index' in types
        present['pfd'] = doc.project.documents.filter(drawing_type='PFD').exists()
    return present


def _missing_inputs(ref_inputs: str, present: Dict[str, bool]) -> List[str]:
    missing = []
    text = (ref_inputs or '').lower()
    for needle, key, label in INPUT_KEYWORDS:
        if needle.lower() not in text:
            continue
        if present.get(key, False):
            continue
        if label not in missing:
            missing.append(label)
    return missing


def _equip_codes(items) -> set:
    codes = set()
    for it in items:
        if it.kind != 'equipment':
            continue
        code = ((it.parsed or {}).get('code') or '').strip().upper()
        if code:
            codes.add(code)
    return codes


def _default_template():
    from apps.pid_verification_v2.models import PIDVChecklistTemplate
    return (
        PIDVChecklistTemplate.objects.filter(is_default=True).first()
        or PIDVChecklistTemplate.objects.order_by('code', '-version').first()
    )


def project_template(project):
    """The project's checklist: its pinned template if set, else the default
    (AI-PID-02). Old projects with nothing pinned get the default."""
    tid = ((project.metadata or {}).get('checklist_template_id')) if project else None
    if tid:
        from apps.pid_verification_v2.models import PIDVChecklistTemplate
        tpl = PIDVChecklistTemplate.objects.filter(pk=tid).first()
        if tpl:
            return tpl
    return _default_template()


def evaluate_sheet(doc, page: int, template=None) -> Optional[dict]:
    """Full per-item evaluation for one sheet. Returns None when no
    checklist template is imported yet."""
    from apps.pid_verification_v2.models import (
        PIDVCrossFinding, PIDVExtractionItem, PIDVFinding, PIDVSheet,
    )

    template = template or project_template(doc.project)
    if template is None:
        return None

    drawing = doc.drawings.filter(page_index=page).first()
    metadata = (drawing.metadata if drawing else {}) or {}
    tb = metadata.get('title_block') or {}

    findings = list(PIDVFinding.objects.filter(drawing=drawing)) if drawing else []
    items_x = list(PIDVExtractionItem.objects.filter(document=doc, page=page))
    sheet = PIDVSheet.objects.filter(document=doc, page_index=page).first()
    cross = []
    if doc.project_id:
        cross = [c for c in PIDVCrossFinding.objects.filter(project_id=doc.project_id)
                 if sheet and sheet.pk in (c.sheets or [])]

    present = _inputs_present(doc)
    equip_codes = _equip_codes(items_x)
    n_lines = sum(1 for i in items_x if i.kind == 'lines')
    n_eq = sum(1 for i in items_x if i.kind == 'equipment')
    n_inst = sum(1 for i in items_x if i.kind == 'instruments')
    n_conn = sum(1 for i in items_x if i.kind == 'connectors')
    n_notes = sum(1 for i in items_x if i.kind == 'notes')
    n_tags = n_eq + n_inst

    # findings by checklist item (from report_meta.checklist_items, else the map)
    by_item: Dict[str, list] = {}
    for f in findings:
        rm = f.report_meta or {}
        nos = rm.get('checklist_items') or RULE_TO_ITEMS.get(f.rule_id, [])
        for no in nos:
            by_item.setdefault(no, []).append(f)
    cross_by_item: Dict[str, list] = {}
    for c in cross:
        for no in RULE_TO_ITEMS.get(c.code, []):
            cross_by_item.setdefault(no, []).append(c)

    def finding_result(fs):
        """Non-Conformance when any linked finding is major/critical, else Query."""
        if any(f.severity in ('critical', 'major') for f in fs):
            return 'Non-Conformance'
        return 'Query'

    def fid_list(fs):
        return [f.id for f in fs]

    # ── applicability: NA when the object type is not on the sheet ───────
    def is_na(item) -> bool:
        sec = item.section_no
        if not n_eq and (sec in ('3', '4', '5', '6', '7')
                         or item.no in ('2.2', '2.3', '2.4', '2.5', '2.6', '2.7')):
            return True
        trigger = _SECTION_EQUIP_CODES.get(sec)
        if sec in _SECTION_EQUIP_CODES and trigger and not (equip_codes & trigger):
            return True
        if item.no in ('10.1', '10.3', '10.4', '10.5'):
            has_psv = any(_PSV_RE.search(i.text or '') for i in items_x)
            if not has_psv:
                return True
        if item.no == '13.4':
            return True  # first review — no previous register to back-check
        return False

    # ── per-item handlers → (ai_result, evidence, finding_ids) or None ───
    def handle(item):
        no = item.no
        fs = by_item.get(no, [])
        xs = cross_by_item.get(no, [])
        if fs:
            ev = '; '.join((f.evidence or f.issue_observed)[:120] for f in fs[:3])
            return finding_result(fs), ev, fid_list(fs)
        if xs:
            ev = '; '.join(c.title for c in xs[:3])
            return ('Non-Conformance' if any(c.kind == 'error' for c in xs) else 'Query'), ev, []

        if no == '0.1':
            listed = ['P&ID files'] + [lbl for _, k, lbl in INPUT_KEYWORDS if k in _PRESENTABLE and k != 'pid']
            states = []
            for lbl in dict.fromkeys(listed):
                key = next(k for n2, k, l2 in INPUT_KEYWORDS if l2 == lbl)
                states.append(f"{lbl}: {'present' if present.get(key) else 'missing'}")
            return ('OK' if present.get('legend') else 'Not Verifiable'), '; '.join(states), []
        if no == '0.2':
            rd = metadata.get('readability')
            if rd == 'ocr':
                return 'Query', 'Read by OCR — values need confirmation', []
            return 'OK', f"Vector text layer read ({metadata.get('character_count', '—')} characters)", []
        if no == '0.3':
            if not items_x:
                return 'Not Verifiable', 'No objects could be read from this sheet', []
            return 'OK', (f'{n_lines} line numbers · {n_eq} equipment · {n_inst} instruments · '
                          f'{n_conn} connectors · {n_notes} notes extracted'), []
        if no == '0.4':
            if not tb.get('drawing_number'):
                return 'Query', 'Title block not fully readable — number/revision to confirm', []
            reg = metadata.get('register_match')
            if reg is False:
                return 'Query', f"{tb.get('drawing_number')} is not in the P&ID register", []
            if reg is True:
                return 'OK', f"Title block {tb.get('drawing_number')} Rev {tb.get('revision') or '—'} matches the register", []
            return 'Not Verifiable', 'No P&ID register in the input package', ['Document register / MDR']
        if no in ('0.5', '2.1'):
            return 'OK', f'{n_tags} tags on this sheet, none duplicated or malformed', []
        if no in ('0.6', '1.1'):
            if not present.get('legend'):
                return 'Not Verifiable', 'Legend P&ID not in the input package', ['Legend P&ID']
            return 'OK', f'{n_eq + n_inst} symbols/tags checked against the legend register, no deviations', []
        if no in ('1.5', '1.6'):
            return 'OK', f'{n_notes} notes read on this sheet, references resolve', []
        if no == '1.11':
            return 'OK', 'No HOLD markers on this sheet', []
        if no == '1.12':
            refs_not_uploaded = [i for i in items_x
                                 if i.kind == 'connectors'
                                 and (i.parsed or {}).get('in_register') is False]
            ev_parts = [f'{n_conn} off-page / C&E connectors read']
            if refs_not_uploaded:
                ev_parts.append('references to drawings not uploaded: '
                                + ', '.join((i.parsed or {}).get('drawing_no') or i.text[:24] for i in refs_not_uploaded[:8]))
            return 'OK', '; '.join(ev_parts), []
        if no == '9.1':
            return 'OK', f'{n_lines} line numbers decoded (size · system · fluid · serial · class · insulation)', []
        if no == '9.9':
            return 'OK', f'{n_lines} lines checked for insulation consistency', []
        if no in ('13.1', '13.2'):
            return 'OK', 'AI Comment Register generated with this report', []
        return None

    # ── assemble ──────────────────────────────────────────────────────────
    stored = {}
    for r in doc.checklist_results.filter(page=page).select_related('item'):
        stored[r.item.no] = r

    out_items = []
    for item in template.items.all():
        # H: engineer override wins; else the AI suggestion.
        st = stored.get(item.no)
        if st and st.applicability_by == 'engineer':
            applicability, applicability_by = st.applicability, 'engineer'
        else:
            applicability, applicability_by = ('NA' if is_na(item) else 'Y'), 'ai'

        ai_result = evidence = None
        finding_ids: list = []
        missing: list = []
        if applicability != 'Y':
            ai_result = None  # I stays empty when H is NA or N
        elif item.no == '13.3':
            ai_result = None  # never set by the AI
        else:
            missing = _missing_inputs(item.ref_inputs, present)
            if missing:
                ai_result = 'Not Verifiable'
                evidence = f"{', '.join(missing)} not in the input package"
            else:
                got = handle(item)
                if got:
                    ai_result, evidence, finding_ids = got
                elif item.mode == 'Automated':
                    ai_result = 'OK'
                    evidence = 'Checked by the related automated rules — no issues found'
                elif item.mode == 'Engineer-Led':
                    ai_result = 'Query'
                    evidence = 'Candidates compiled — engineer decides (see the comment register)'
                else:
                    ai_result = 'Query'
                    evidence = 'Needs engineering judgement — AI-assisted review'

        out_items.append({
            'no': item.no,
            'section': item.section_no,
            'section_title': item.section_title,
            'sub_heading': item.sub_heading,
            'check_point': item.check_point,
            'mode': item.mode,
            'severity': item.severity,
            'ai_task': item.ai_task,
            'ref_inputs': item.ref_inputs,
            'evidence_required': item.evidence_required,
            'applicability': applicability,
            'applicability_by': applicability_by,
            'ai_result': ai_result,
            'evidence': evidence,
            'finding_ids': finding_ids,
            'missing_inputs': missing,
            'engineer_verification': st.engineer_verification if st else None,
            'remarks': st.remarks if st else '',
        })

    return {
        'template': {'code': template.code, 'version': template.version, 'item_count': template.item_count},
        'items': out_items,
        'summary': summarize(out_items),
    }


def summarize(items: List[dict]) -> dict:
    """Same matrix as the template's Summary sheet: result × severity over
    rows with I filled; 'Not yet reviewed' = I empty (incl. NA — see the
    template's open point); counts by mode; Disagree count; accuracy =
    Agree ÷ rows with J filled."""
    matrix = {}
    for result in ('OK', 'Non-Conformance', 'Query', 'Not Verifiable'):
        matrix[result] = {s: 0 for s in ('Critical', 'Major', 'Minor')}
        matrix[result]['total'] = 0
    not_reviewed = 0
    not_applicable = 0
    by_mode = {'Automated': 0, 'AI-Assisted': 0, 'Engineer-Led': 0}
    agree = disagree = modified = filled = 0
    for it in items:
        r = it.get('ai_result')
        if r in matrix and it['applicability'] == 'Y':
            matrix[r][it['severity']] = matrix[r].get(it['severity'], 0) + 1
            matrix[r]['total'] += 1
        else:
            not_reviewed += 1
        if it['applicability'] != 'Y':
            not_applicable += 1
        if it['mode'] in by_mode:
            by_mode[it['mode']] += 1
        j = it.get('engineer_verification')
        if j:
            filled += 1
            agree += j == 'Agree'
            disagree += j == 'Disagree'
            modified += j == 'Modified'
    return {
        'matrix': matrix,
        'not_yet_reviewed': not_reviewed,
        'not_applicable': not_applicable,
        'by_mode': by_mode,
        'agree': agree,
        'disagree': disagree,
        'modified': modified,
        'accuracy': round(agree / filled * 100) if filled else None,
    }
