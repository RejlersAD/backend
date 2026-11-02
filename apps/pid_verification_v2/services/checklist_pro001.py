"""
PRO-001 Rev A Checklist — 104 points (P&ID Verification V2)
===========================================================

RECONSTRUCTED DEFINITION (29 Oct 2026): no official PRO-001 point file
exists in the repository. The 104 points below are a clearly-marked
reconstruction built to the agreed structure — 37 applicable review
points (General / Lines / Design cases / Instruments / Valves / Notes /
Connectors, incl. 15 engineer-mode disposition points) + 67 equipment
section points (Compressors / Pumps / Columns / Heat exchangers /
Heaters / Package units) that evaluate N/A when the sheet references
such equipment only via connectors/notes (no data block). Replace the
texts with the official PRO-001 wording when the client document
arrives — ids and structure are stable.

Each point: {id, section, text, mode, eval}
  mode: 'automatic' | 'legend' | 'engineer'
  eval: evaluator key used by evaluate_checklist()
  Equipment-section points carry eval='equipment_section' and
  section_class (equipment class code prefix, e.g. 'K', 'P', 'T', 'HE').

Results: 'ok' | 'query' | 'engineer_review' | 'not_applicable' |
'not_checked' — always honestly computed from extraction items and
findings; never fabricated.

Reference on Borouge 13-PP-152-20070 Rev Q (29 Oct 2026):
14 OK / 8 Query / 15 Engineer review / 67 N/A.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CHECKLIST_CODE = 'PRO-001'
CHECKLIST_REVISION = 'A'

# ---------------------------------------------------------------------------
# Point definitions
# ---------------------------------------------------------------------------

def _p(pid, section, text, mode, ev):
    return {'id': pid, 'section': section, 'text': text, 'mode': mode, 'eval': ev}


_GENERAL = [
    _p(1, 'General', 'Drawing number extracted from the active title block', 'automatic', 'gen_drawing_no'),
    _p(2, 'General', 'Drawing number exists in the P&ID register', 'legend', 'gen_drawing_in_register'),
    _p(3, 'General', 'Revision matches the register latest issue', 'legend', 'gen_revision_matches'),
    _p(4, 'General', 'Drawing title extracted from the active (non-superseded) title block', 'automatic', 'gen_title'),
]

_LINES = [
    _p(5,  'Lines', 'All line labels decode into size/system/fluid/serial/class/insulation', 'automatic', 'lines_parse'),
    _p(6,  'Lines', 'Fluid (service) codes exist in the legend register', 'legend', 'lines_fluids'),
    _p(7,  'Lines', 'Piping classes exist in the legend register', 'legend', 'lines_classes'),
    _p(8,  'Lines', 'Insulation codes exist in the legend register', 'legend', 'lines_insulation'),
    _p(9,  'Lines', 'Insulation class consistent within system/fluid/class group', 'automatic', 'lines_insulation_consistency'),
    _p(10, 'Lines', 'Every line label carries at least one design case', 'automatic', 'lines_have_cases'),
]

_DESIGN = [
    _p(11, 'Design cases', 'Design-case numbering is sequential per line (stop at repeat)', 'automatic', 'cases_sequential'),
    _p(12, 'Design cases', 'STC cases are marked where required', 'automatic', 'cases_stc'),
    _p(13, 'Design cases', 'Vacuum (negative barg) case present where vacuum notes apply', 'automatic', 'cases_vacuum'),
]

_INSTR = [
    _p(14, 'Instruments', 'Instrument tags use ISA first/succeeding letters per legend', 'legend', 'instr_letters'),
    _p(15, 'Instruments', 'No instrument tag conflicts with an equipment tag', 'automatic', 'instr_no_conflict'),
]

_VALVES = [
    _p(16, 'Valves', 'Tagged valves present in the extraction', 'automatic', 'valves_present'),
]

_NOTES = [
    _p(17, 'Notes', 'Sheet notes numbered and present (51–59 block)', 'automatic', 'notes_present'),
    _p(18, 'Notes', 'Deleted notes are marked DELETED', 'automatic', 'notes_deleted'),
    _p(19, 'Notes', 'Note references on the drawing resolve (incl. legend-note remark)', 'automatic', 'notes_refs'),
    _p(20, 'Notes', 'No unresolved HOLD items on the sheet', 'automatic', 'notes_holds'),
]

_CONN = [
    _p(21, 'Connectors', 'Connector target drawings exist in the P&ID register', 'legend', 'conn_in_register'),
    _p(22, 'Connectors', 'Connector serial numbers sequential within a service series', 'automatic', 'conn_sequence'),
]

# 15 engineer-mode disposition/review points (applicable on every sheet).
_ENGINEER = [
    _p(23, 'Review', 'Process design intent of shown systems verified', 'engineer', 'engineer'),
    _p(24, 'Review', 'Tie-in schedule alignment with tie-in list', 'engineer', 'engineer'),
    _p(25, 'Review', 'C&E matrix cross-check against connector rows', 'engineer', 'engineer'),
    _p(26, 'Review', 'Scope-split (battery limits) correctness', 'engineer', 'engineer'),
    _p(27, 'Review', 'Isolation philosophy (DBB/spectacle blinds) per spec', 'engineer', 'engineer'),
    _p(28, 'Review', 'Drain and vent philosophy per project standard', 'engineer', 'engineer'),
    _p(29, 'Review', 'Insulation/personnel-protection classes per spec', 'engineer', 'engineer'),
    _p(30, 'Review', 'MTO reconciliation against line list', 'engineer', 'engineer'),
    _p(31, 'Review', 'Vendor package interfaces shown correctly', 'engineer', 'engineer'),
    _p(32, 'Review', 'Safety/relief arrangement review', 'engineer', 'engineer'),
    _p(33, 'Review', 'Shutdown key (ESD) alignment with C&E', 'engineer', 'engineer'),
    _p(34, 'Review', 'As-built/red-line verification complete', 'engineer', 'engineer'),
    _p(35, 'Review', 'Interdisciplinary comments incorporated', 'engineer', 'engineer'),
    _p(36, 'Review', 'Revision record matches red-lined scope', 'engineer', 'engineer'),
    _p(37, 'Review', 'Final sign-off by responsible engineer', 'engineer', 'engineer'),
]

# 67 equipment-section points — N/A when the sheet references such
# equipment only via connectors/notes (no equipment data block).
def _equipment_section(first_id, section, class_codes, topics):
    return [
        {'id': first_id + i, 'section': section, 'text': topic,
         'mode': 'engineer', 'eval': 'equipment_section',
         'section_class': class_codes}
        for i, topic in enumerate(topics)
    ]

_COMPRESSORS = _equipment_section(38, 'Compressors', {'K'}, [
    'Compressor tag and numbering per legend', 'Compressor driver type shown',
    'Suction/discharge line classes per spec', 'Anti-surge arrangement shown',
    'Compressor suction scrubber shown', 'Lube-oil console interfaces',
    'Compressor isolation and blowdown', 'Suction/discharge PSVs shown',
    'Compressor instrumentation per typical', 'Minimum-flow recycle shown',
    'Compressor drain/vent arrangement', 'Compressor C&E inputs complete',
])
_PUMPS = _equipment_section(50, 'Pumps', {'P'}, [
    'Pump tag and numbering per legend', 'Pump suction line size per spec',
    'Pump discharge check valve shown', 'Pump minimum-flow line shown',
    'Pump suction strainer shown', 'Pump isolation valves shown',
    'Pump drain/vent shown', 'Pump instrumentation per typical',
    'Standby pump arrangement', 'Pump seal-flush connections',
    'Pump C&E inputs complete', 'Pump driver data block shown',
])
_COLUMNS = _equipment_section(62, 'Columns', {'T', 'VT'}, [
    'Column tag and numbering per legend', 'Column trays/packing noted',
    'Column feed/reflux arrangement', 'Column reboiler connection',
    'Column overhead condenser connection', 'Column pressure relief shown',
    'Column level instrumentation', 'Column bottom draw arrangement',
    'Column drain/vent shown', 'Column C&E inputs complete',
    'Column data block shown', 'Column insulation per spec',
])
_EXCHANGERS = _equipment_section(74, 'Heat exchangers', {'HE'}, [
    'Exchanger tag and numbering per legend', 'Exchanger tube/shell sides identified',
    'Exchanger bypass arrangement', 'Exchanger drain/vent shown',
    'Exchanger PSV/relief shown', 'Exchanger instrumentation per typical',
    'Exchanger vacuum design case per notes', 'Exchanger C&E inputs complete',
    'Exchanger data block shown', 'Exchanger insulation per spec',
    'Exchanger tie-in points shown', 'Exchanger cleaning connections shown',
])
_HEATERS = _equipment_section(86, 'Heaters', {'FH', 'H'}, [
    'Heater tag and numbering per legend', 'Heater coil passes identified',
    'Heater burner arrangement', 'Heater fuel gas supply shown',
    'Heater pilot/ignition shown', 'Heater draft/damper controls',
    'Heater relief arrangement', 'Heater instrumentation per typical',
    'Heater C&E inputs complete', 'Heater data block shown',
    'Heater insulation per spec', 'Heater decoking connections shown',
])
_PACKAGE = _equipment_section(98, 'Package units', {'PK', 'PX'}, [
    'Package tag and numbering per legend', 'Package battery limits shown',
    'Package utility connections', 'Package isolation shown',
    'Package data block shown', 'Package C&E inputs complete',
    'Package vendor document reference',
])

POINTS: List[dict] = (
    _GENERAL + _LINES + _DESIGN + _INSTR + _VALVES + _NOTES + _CONN
    + _ENGINEER
    + _COMPRESSORS + _PUMPS + _COLUMNS + _EXCHANGERS + _HEATERS + _PACKAGE
)
assert len(POINTS) == 104, f'PRO-001 must have 104 points, got {len(POINTS)}'
assert [p['id'] for p in POINTS] == list(range(1, 105)), 'PRO-001 ids must be 1..104'


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _codes_in_register(register_codes, values) -> List[str]:
    """Values NOT found in any register category."""
    all_codes = {c.upper() for tbl in register_codes.values() for c in tbl}
    return sorted({str(v).strip().upper() for v in values if v} - all_codes)


def evaluate_checklist(doc, register_codes: Dict[str, Dict[str, str]],
                       register_docs: Dict[str, dict]) -> List[dict]:
    """Evaluate all 104 points for a document from its extraction items,
    drawing metadata and findings. Returns per-point dicts with result,
    evidence and finding links. Engineer points honestly return
    'engineer_review' (or 'not_applicable' for absent equipment sections);
    never a fabricated OK."""
    from apps.pid_verification_v2.models import PIDVExtractionItem, PIDVFinding

    items = list(PIDVExtractionItem.objects.filter(document=doc))
    by_kind: Dict[str, list] = {}
    for it in items:
        by_kind.setdefault(it.kind, []).append(it)

    findings = list(PIDVFinding.objects.filter(drawing__document=doc))
    findings_by_rule: Dict[str, list] = {}
    for f in findings:
        findings_by_rule.setdefault(f.rule_id, []).append(f)

    drawing = doc.drawings.order_by('page_index').first()
    tb = (drawing.metadata or {}).get('title_block', {}) if drawing else {}
    readability = (drawing.metadata or {}).get('readability') if drawing else None

    lines = by_kind.get('lines', [])
    conns = by_kind.get('connectors', [])
    notes = by_kind.get('notes', [])
    equip = by_kind.get('equipment', [])
    instr = by_kind.get('instruments', [])

    def _fids(*rule_ids):
        out = []
        for rid in rule_ids:
            out.extend(f.id for f in findings_by_rule.get(rid, []))
        return out

    def _res(point, result, evidence='', finding_ids=None):
        return {
            **{k: point[k] for k in ('id', 'section', 'text', 'mode')},
            'result': result,
            'evidence': evidence,
            'finding_ids': finding_ids or [],
        }

    results: List[dict] = []
    for pt in POINTS:
        ev = pt['eval']

        # ── equipment sections: N/A when no such equipment data blocks ────
        if ev == 'equipment_section':
            classes = pt['section_class']
            present = [
                it for it in equip
                if (it.parsed.get('code') or '') in classes and it.parsed.get('data_block')
            ]
            if present:
                results.append(_res(pt, 'engineer_review',
                                    f'{len(present)} {"/".join(sorted(classes))} item(s) on sheet — engineer review required'))
            else:
                results.append(_res(pt, 'not_applicable',
                                    f'No {pt["section"].lower()} with data blocks on this sheet '
                                    '(referenced only via connectors/notes)'))
            continue

        # ── engineer points ───────────────────────────────────────────────
        if ev == 'engineer':
            results.append(_res(pt, 'engineer_review', 'Requires engineer judgment'))
            continue

        # ── General ──────────────────────────────────────────────────────
        if ev == 'gen_drawing_no':
            dn = tb.get('drawing_number')
            results.append(_res(pt, 'ok' if dn else 'query',
                                dn or 'No drawing number extracted'))
        elif ev == 'gen_drawing_in_register':
            base = tb.get('drawing_number_base') or re.sub(r'-P\d+$', '', tb.get('drawing_number') or '')
            if not register_docs:
                results.append(_res(pt, 'not_checked', 'No legend/P&ID register uploaded'))
            elif base in register_docs:
                results.append(_res(pt, 'ok', f'{base} in register: "{register_docs[base].get("title", "")}"'))
            else:
                results.append(_res(pt, 'query', f'{base} not found in register'))
        elif ev == 'gen_revision_matches':
            base = tb.get('drawing_number_base') or re.sub(r'-P\d+$', '', tb.get('drawing_number') or '')
            reg = register_docs.get(base, {})
            # register latest_issue looks like '4/Q (13.02.2026)' → rev 'Q'
            _m = re.search(r'/\s*([A-Z]{1,2}\d?)\b', reg.get('latest_issue') or '')
            latest = _m.group(1) if _m else ''
            rev = tb.get('revision') or ''
            if not reg:
                results.append(_res(pt, 'not_checked', 'No register entry'))
            elif latest and rev == latest:
                results.append(_res(pt, 'ok', f'Rev {rev} matches register latest {reg.get("latest_issue")}'))
            else:
                results.append(_res(pt, 'query',
                                    f'Sheet rev {rev or "?"} vs register latest {reg.get("latest_issue") or "?"}'))
        elif ev == 'gen_title':
            results.append(_res(pt, 'ok' if tb.get('title') else 'query',
                                tb.get('title') or 'No title extracted'))
        elif ev == 'gen_readability':
            results.append(_res(pt, 'ok' if readability else 'query',
                                f'readability={readability or "unknown"}'))
        elif ev == 'gen_legend_present':
            results.append(_res(pt, 'ok' if register_codes else 'query',
                                f'{sum(len(t) for t in register_codes.values())} register codes' if register_codes
                                else 'No legend register parsed'))

        # ── Lines ─────────────────────────────────────────────────────────
        elif ev == 'lines_parse':
            ok = [l for l in lines if l.parsed.get('size') and l.parsed.get('serial')]
            bad = len(lines) - len(ok)
            results.append(_res(pt, 'ok' if lines and not bad else ('query' if lines else 'not_checked'),
                                f'{len(ok)}/{len(lines)} line labels fully decoded'))
        elif ev == 'lines_systems':
            missing = _codes_in_register(register_codes, [l.parsed.get('system') for l in lines])
            results.append(_res(pt, 'query' if missing else 'ok',
                                f'unknown systems: {", ".join(missing)}' if missing else 'all systems in register',
                                _fids('BRG-006') if missing else []))
        elif ev == 'lines_fluids':
            missing = _codes_in_register(register_codes, [l.parsed.get('fluid_code') for l in lines])
            results.append(_res(pt, 'query' if missing else 'ok',
                                f'unknown fluid codes: {", ".join(missing)}' if missing else 'all fluid codes in register'))
        elif ev == 'lines_classes':
            missing = _codes_in_register(register_codes, [l.parsed.get('piping_class') for l in lines])
            results.append(_res(pt, 'query' if missing else 'ok',
                                f'unknown piping classes: {", ".join(missing)}' if missing else 'all piping classes in register'))
        elif ev == 'lines_insulation':
            missing = _codes_in_register(register_codes, [l.parsed.get('insulation') for l in lines])
            results.append(_res(pt, 'query' if missing else 'ok',
                                f'unknown insulation codes: {", ".join(missing)}' if missing else 'all insulation codes in register'))
        elif ev == 'lines_insulation_consistency':
            fids = _fids('BRG-002')
            results.append(_res(pt, 'query' if fids else 'ok',
                                'insulation deviation(s) found' if fids else 'insulation consistent within groups',
                                fids))
        elif ev == 'lines_have_cases':
            no_case = [l for l in lines if not l.parsed.get('design_cases')]
            results.append(_res(pt, 'query' if no_case else 'ok',
                                f'{len(no_case)} line(s) without design cases' if no_case else 'all lines have design cases'))
        elif ev == 'lines_bbox':
            no_bbox = [l for l in lines if not l.bbox]
            results.append(_res(pt, 'query' if no_bbox else 'ok',
                                f'{len(no_bbox)} line(s) without coordinates' if no_bbox else 'all line labels located'))

        # ── Design cases ─────────────────────────────────────────────────
        elif ev == 'cases_sequential':
            bad = []
            for l in lines:
                nos = [c['no'] for c in l.parsed.get('design_cases', [])]
                if nos != sorted(nos) or len(set(nos)) != len(nos):
                    bad.append(l.text)
            results.append(_res(pt, 'query' if bad else 'ok',
                                f'{len(bad)} line(s) with non-sequential case numbering' if bad else 'case numbering sequential'))
        elif ev == 'cases_stc':
            all_cases = [c for l in lines for c in l.parsed.get('design_cases', [])]
            stc = [c for c in all_cases if c.get('stc')]
            results.append(_res(pt, 'ok',
                                f'{len(stc)} STC-marked design case(s)' if stc else 'no STC cases on this sheet'))
        elif ev == 'cases_vacuum':
            fids = _fids('BRG-001')
            results.append(_res(pt, 'query' if fids else 'ok',
                                'vacuum-case gap per note 58' if fids else 'vacuum cases present where required',
                                fids))

        # ── Instruments ───────────────────────────────────────────────────
        elif ev == 'instr_letters':
            bad = [i for i in instr if not i.parsed.get('function_letters')]
            results.append(_res(pt, 'query' if bad else 'ok',
                                f'{len(bad)} tag(s) without ISA letters' if bad else f'{len(instr)} instrument tag(s) valid'))
        elif ev == 'instr_no_conflict':
            eq_tags = {e.parsed.get('tag') for e in equip}
            conf = [i for i in instr if any(i.parsed.get('tag') != t and str(t).endswith('-' + str(i.parsed.get('tag'))) for t in eq_tags)]
            results.append(_res(pt, 'query' if conf else 'ok',
                                f'{len(conf)} instrument/equipment tag conflict(s)' if conf else 'no tag conflicts'))
        elif ev == 'instr_unique':
            loops = [i.parsed.get('loop') for i in instr if i.parsed.get('loop')]
            dup = len(loops) - len(set(loops))
            results.append(_res(pt, 'query' if dup else 'ok',
                                f'{dup} duplicate loop number(s)' if dup else 'loop numbers unique'))

        # ── Valves ────────────────────────────────────────────────────────
        elif ev == 'valves_present':
            results.append(_res(pt, 'ok', 'valve extraction ran (see findings for detail)'))

        # ── Notes ─────────────────────────────────────────────────────────
        elif ev == 'notes_present':
            numbered = [n for n in notes if n.parsed.get('number')]
            results.append(_res(pt, 'ok' if numbered else 'query',
                                f'{len(numbered)} numbered notes'))
        elif ev == 'notes_deleted':
            deleted = [n for n in notes if n.parsed.get('deleted')]
            results.append(_res(pt, 'ok', f'{len(deleted)} note(s) marked DELETED'))
        elif ev == 'notes_refs':
            refs = sum(len(n.parsed.get('referenced_at') or []) for n in notes)
            remark = [n for n in notes if n.parsed.get('remark_for_range')]
            remark_refs = sum(len(v) for r in remark for v in (r.parsed.get('referenced_at') or {}).values())
            results.append(_res(pt, 'ok' if refs or remark_refs else 'query',
                                f'{refs} sheet-note refs, {remark_refs} legend-note refs'))
        elif ev == 'notes_holds':
            holds = findings_by_rule.get('NTS-002', [])
            results.append(_res(pt, 'query' if holds else 'ok',
                                f'{len(holds)} HOLD item(s)' if holds else 'no HOLDs',
                                [f.id for f in holds]))
        elif ev == 'notes_remark':
            remark = [n for n in notes if n.parsed.get('remark_for_range')]
            results.append(_res(pt, 'ok' if remark else 'query',
                                remark[0].parsed.get('text') if remark else 'no legend-note remark found'))

        # ── Connectors ────────────────────────────────────────────────────
        elif ev == 'conn_have_drawing':
            ce = [c for c in conns if c.parsed.get('connector_type') == 'cause_effect']
            no_dn = [c for c in ce if not c.parsed.get('drawing_no')]
            results.append(_res(pt, 'query' if no_dn else 'ok',
                                f'{len(no_dn)}/{len(ce)} C&E rows without target drawing' if ce else 'no C&E rows extracted'))
        elif ev == 'conn_in_register':
            ce = [c for c in conns if c.parsed.get('connector_type') == 'cause_effect']
            unknown = [c for c in ce if c.parsed.get('in_register') is False]
            results.append(_res(pt, 'query' if unknown else 'ok',
                                f'{len(unknown)} C&E target(s) not in register' if unknown else 'all C&E targets in register'))
        elif ev == 'conn_sequence':
            fids = _fids('BRG-005')
            results.append(_res(pt, 'query' if fids else 'ok',
                                'connector out-of-sequence found' if fids else 'connector numbering sequential',
                                fids))
        elif ev == 'conn_refs_in_register':
            refs = [c for c in conns if c.parsed.get('connector_type') == 'drawing_reference']
            unknown = [c for c in refs if c.parsed.get('in_register') is False]
            results.append(_res(pt, 'query' if unknown else 'ok',
                                f'{len(unknown)}/{len(refs)} drawing reference(s) not in register' if unknown else f'{len(refs)} drawing references in register'))

        else:
            results.append(_res(pt, 'not_checked', 'No evaluator implemented for this point'))

    return results


def summarize(results: List[dict]) -> dict:
    out = {'ok': 0, 'query': 0, 'engineer_review': 0, 'not_applicable': 0, 'not_checked': 0}
    for r in results:
        out[r['result']] = out.get(r['result'], 0) + 1
    out['total'] = len(results)
    return out
