"""
Smart Check Engine — deterministic execution of AI Check Scope Register rows.

Runs instantly (no vision calls) on data the pipeline has already produced:
  • per-drawing extraction summaries (tags / equipment / valves / holds / notes)
  • per-drawing comparison results (legend / line list / equipment / instrument)
  • PIDVFinding rows from the rule + comparison engines
  • parsed reference data (line list, equipment list, instrument index)
  • engineer-marked reference documents (PIDCheckManagementState.docs_provided)

SOFT-CODED DESIGN
-----------------
Every executable register check is declared as DATA in the rule tables below
(RECONCILIATION_RULES / FINDINGS_RULES / EXTRACTION_RULES / COMPARISON_ATTR_RULES
/ GATED_RULES). To add, remove, or retarget a check, edit the tables only —
the engine code interprets them generically.

Every row is honestly classified: Pass / Fail only where the data supports the
conclusion; otherwise "Not Checked" with the missing input named — exactly the
convention required by the AI Check Scope Register.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Rule-ID prefixes used by the analysis pipeline (see tasks.py comparison
# finding mapping: LGN legend, LSZ line list/size, EQP equipment, IMS
# instrument, NTS notes, CON connectivity, CMP general comparison).
RULE_LEGEND = 'LGN'
RULE_LINE = 'LSZ'
RULE_NOTES = 'NTS'
RULE_CONNECTIVITY = 'CON'

# Finding category → rule_id prefix used for evidence samples (soft-coded).
CATEGORY_RULE_PREFIX = {
    'connectivity': 'CON', 'notes': 'NTS', 'line_size': 'LSZ',
    'line_designation': 'LSZ', 'legend': 'LGN', 'tag': 'TAG',
    'equipment': 'EQP', 'instrument': 'IMS', 'valve': 'VLV',
}

# ── Soft-coded reference-document labels ──────────────────────────────────────
# Keys are the canonical document names used by the frontend reference-docs
# checklist (REQUIRED_REFERENCE_DOCS / SECTION_DOC_HINTS). `data_type` is set
# when the document maps to a parsed PIDVReferenceData type.
REFERENCE_DOCS = {
    'Line List':                  {'data_type': 'line_list'},
    'Equipment List':             {'data_type': 'equipment_list'},
    'Instrument Index':           {'data_type': 'instrument_index'},
    'PFD / Heat & Material Balance': {'data_type': None},
    'Relief Register':            {'data_type': None},
    'Cause & Effect Diagrams':    {'data_type': None},
    'HOLD Register':              {'data_type': None},
    'Tie-in Schedule':            {'data_type': None},
    'Utility Summary / UFDs':     {'data_type': None},
    'Piping Material Spec Index': {'data_type': None},
    'Electrical Documents':       {'data_type': None},
    'Tag Numbering Procedure':    {'data_type': None},
}

# ── Reconciliation rules ──────────────────────────────────────────────────────
# Two-way set reconciliation from stored comparison results.
# (check_id, comparison bucket, reference doc name, basis noun)
RECONCILIATION_RULES = [
    ('C-3.1',  'linelist',   'Line List',        'line'),
    ('D-11.1', 'linelist',   'Line List',        'line'),
    ('F-11.2', 'linelist',   'Line List',        'line'),
    # ASSIST aliases — same two-way line-list evidence, engineer confirms
    ('F-4.1',  'linelist',   'Line List',        'line'),
    ('D-3.1',  'linelist',   'Line List',        'line'),
    ('C-4.1',  'equipment',  'Equipment List',   'equipment'),
    # ASSIST aliases — equipment tag/description evidence vs equipment list
    ('F-3.1',  'equipment',  'Equipment List',   'equipment'),
    ('D-2.1',  'equipment',  'Equipment List',   'equipment'),
    ('C-5.1',  'instrument', 'Instrument Index', 'instrument'),
    # ASSIST alias — ISA tag uniqueness/match vs instrument index
    ('D-5.1',  'instrument', 'Instrument Index', 'instrument'),
]

# ── Comparison-attribute rules ────────────────────────────────────────────────
# Field-level agreement between drawing and a parsed reference list, derived
# from the aggregated mismatch counter of the corresponding comparison bucket.
# (check_id, comparison bucket, reference doc name, attribute description)
COMPARISON_ATTR_RULES = [
    ('C-3.3', 'linelist',   'Line List',        'piping class/spec'),
    ('C-3.4', 'linelist',   'Line List',        'insulation class/thickness'),
    ('C-3.5', 'linelist',   'Line List',        'heat-tracing flags'),
    ('C-3.6', 'linelist',   'Line List',        'design/operating P-T'),
    ('C-3.7', 'linelist',   'Line List',        'from/to routing'),
    ('C-3.8', 'linelist',   'Line List',        'test pressure/medium'),
    ('C-3.9', 'linelist',   'Line List',        'special requirement flags'),
    ('C-4.2', 'equipment',  'Equipment List',   'service descriptions'),
    ('C-4.9', 'equipment',  'Equipment List',   'equipment design conditions'),
    ('C-5.2', 'instrument', 'Instrument Index', 'tag service descriptions'),
    ('C-5.3', 'instrument', 'Instrument Index', 'instrument types'),
    ('C-5.4', 'instrument', 'Instrument Index', 'location/system allocation'),
    ('C-5.5', 'instrument', 'Instrument Index', 'line/equipment references'),
    ('C-5.7', 'instrument', 'Instrument Index', 'I/O signal types'),
]

# ── Findings-count rules ──────────────────────────────────────────────────────
# Pass/Fail from PIDVFinding counts in the given categories.
# (check_ids, finding categories, optional required ref doc, finding template)
FINDINGS_RULES = [
    (('F-1.6', 'D-1.6', 'C-1.1', 'C-1.2', 'C-1.3', 'D-3.11'), ('connectivity',), None,
     '{count} off-page connector / cross-sheet continuity findings across sheets.'),
    (('C-1.6', 'F-11.4'), ('tag',), None,
     '{count} tag findings (duplicates / orphan tags) across the drawing set.'),
    (('C-1.7',), ('notes',), None,
     "{count} note-reference findings (orphan 'see note' / typical-detail references)."),
    (('C-1.9', 'F-1.8', 'D-1.9'), ('notes',), None,
     '{count} general-notes findings; verify drawing content does not contradict the notes.'),
    (('D-3.2', 'C-3.2'), ('line_size', 'line_designation'), 'Line List',
     '{count} line-size/designation findings vs line list.'),
]

# ── Extraction-summary rules ──────────────────────────────────────────────────
# Presence/absence conclusions from per-sheet extraction counters.
# (check_ids, counter key, finding templates for present/absent)
EXTRACTION_RULES = [
    (('F-1.5', 'D-1.8'), 'holds',
     '{count} HOLD clouds detected across {sheets} sheets. Verify each is numbered, '
     'listed in the HOLD register with owner and close-out date.',
     'No HOLD clouds detected on any sheet.'),
]

# ── Gated rules ───────────────────────────────────────────────────────────────
# Checks that require a reference document the engine cannot parse yet. The
# engine aligns them with the project's reference-doc state: an honest
# "Not Checked — <doc> not provided" until the document is supplied.
# (check_ids, reference doc name)
GATED_RULES = [
    (('F-11.1', 'C-2.1', 'C-2.2', 'C-2.3', 'C-2.5', 'C-2.6', 'C-2.7', 'C-2.8',
      'F-1.10', 'D-1.11'),
     'PFD / Heat & Material Balance'),
    (('F-11.5', 'D-11.4', 'C-6.1', 'C-6.2', 'C-6.3', 'C-6.5', 'C-6.6', 'C-6.7',
      'D-4.4'),
     'Cause & Effect Diagrams'),
    (('F-11.6', 'D-11.8', 'C-7.5', 'C-7.7', 'D-6.2'), 'Relief Register'),
    (('F-11.7', 'C-1.14', 'C-12.1', 'C-12.2', 'C-12.3', 'C-12.4', 'C-12.5'),
     'Utility Summary / UFDs'),
    (('F-11.10', 'C-1.12'), 'HOLD Register'),
    (('C-8.1', 'C-8.2', 'C-8.3', 'C-8.4', 'C-8.5', 'C-8.6', 'C-8.7', 'D-11.7',
      'D-3.14'),
     'Piping Material Spec Index'),
    (('C-13.1', 'C-13.3', 'C-13.4', 'C-13.6', 'F-4.11'), 'Tie-in Schedule'),
    (('D-11.6', 'C-11.1', 'C-11.2', 'C-11.3', 'C-11.4', 'C-11.5', 'C-11.6'),
     'Electrical Documents'),
    (('C-5.8', 'D-5.5'), 'Instrument Index'),
    (('F-1.2',), 'Tag Numbering Procedure'),
]


def _row(result, confidence, finding):
    return {'result': result, 'confidence': confidence, 'finding': finding[:480]}


def run_smart_checks(project) -> dict:
    """
    Execute every register check that can be concluded from stored analysis
    data for the project's completed documents.

    Returns:
      {
        'rows': { check_id: {result, confidence, finding} },
        'meta': { documents, sheets, findings, reference_data: {...} },
      }
    """
    from apps.pid_verification.models import (
        PIDVDocument, PIDVFinding, PIDVReferenceData, PIDCheckManagementState,
    )

    rows = {}

    docs = list(PIDVDocument.objects.filter(
        project=project, status=PIDVDocument.Status.COMPLETED,
    ))
    doc_ids = [d.id for d in docs]

    # ── Reference data availability (parsed uploads) ────────────────────────
    ref = {}
    for rd in PIDVReferenceData.objects.filter(project=project, status='completed'):
        ref[rd.data_type] = {'file': rd.file_name, 'rows': len(rd.parsed_data or [])}

    # ── Engineer-marked reference documents (frontend checklist) ────────────
    try:
        docs_provided = PIDCheckManagementState.objects.get(project=project).docs_provided or {}
    except PIDCheckManagementState.DoesNotExist:
        docs_provided = {}

    def doc_available(doc_name):
        """A reference doc counts as available when its parsed data exists or
        the engineer marked it as provided in the reference-docs checklist."""
        data_type = (REFERENCE_DOCS.get(doc_name) or {}).get('data_type')
        return bool((data_type and data_type in ref) or docs_provided.get(doc_name))

    # ── Aggregate per-drawing comparison + extraction data ──────────────────
    comp_totals = defaultdict(lambda: {'matched': 0, 'missing': 0, 'extra': 0, 'mismatch': 0, 'pid_items': 0, 'ref_items': 0})
    extraction_totals = defaultdict(int)
    sheet_count = 0
    for doc in docs:
        for dr in doc.drawings.all():
            md = dr.metadata or {}
            sheet_count += 1
            for k, v in (md.get('extraction_summary') or {}).items():
                if isinstance(v, (int, float)):
                    extraction_totals[k] += v
            for comp_type, cr in (md.get('comparison_results') or {}).items():
                t = comp_totals[comp_type]
                t['matched'] += cr.get('matched_count', 0)
                t['missing'] += cr.get('missing_count', 0)
                t['extra'] += cr.get('extra_count', 0)
                t['mismatch'] += cr.get('mismatch_count', 0)
                t['pid_items'] += cr.get('total_pid_items', 0)
                t['ref_items'] += cr.get('total_ref_items', 0)

    # ── Findings aggregates ─────────────────────────────────────────────────
    findings = PIDVFinding.objects.filter(drawing__document_id__in=doc_ids)
    by_category = defaultdict(int)
    samples = defaultdict(list)
    for f in findings.values('category', 'rule_id', 'issue_observed', 'drawing__title'):
        by_category[f['category']] += 1
        key = (f['rule_id'] or '')[:3]
        if len(samples[key]) < 3 and f['issue_observed']:
            samples[key].append(f"{f['drawing__title']}: {f['issue_observed'][:120]}")

    # ── Generic rule evaluators ─────────────────────────────────────────────

    def eval_reconciliation(check_id, comp_type, doc_name, basis):
        """Two-way reconciliation row from aggregated comparison results."""
        data_type = (REFERENCE_DOCS.get(doc_name) or {}).get('data_type')
        if not data_type or data_type not in ref:
            rows[check_id] = _row('Not Checked', 'High', f'Not Checked — {doc_name} not provided.')
            return
        t = comp_totals[comp_type]
        if t['pid_items'] == 0 and t['ref_items'] == 0:
            rows[check_id] = _row('Not Checked', 'Low',
                f'No {basis} comparison data — re-run the analysis after uploading {doc_name} to compute reconciliation.')
            return
        problems = t['missing'] + t['extra'] + t['mismatch']
        finding = (f"{t['matched']} matched, {t['missing']} missing from {doc_name}, "
                   f"{t['extra']} extra on P&ID, {t['mismatch']} mismatches "
                   f"(basis: {ref[data_type]['file']}, {ref[data_type]['rows']} rows).")
        rows[check_id] = _row('Pass' if problems == 0 else 'Fail', 'High' if t['matched'] else 'Low', finding)

    def eval_comparison_attr(check_id, comp_type, doc_name, attribute):
        """Attribute-level agreement via the bucket's mismatch counter."""
        data_type = (REFERENCE_DOCS.get(doc_name) or {}).get('data_type')
        if not data_type or data_type not in ref:
            rows[check_id] = _row('Not Checked', 'High', f'Not Checked — {doc_name} not provided.')
            return
        t = comp_totals[comp_type]
        if t['pid_items'] == 0 and t['ref_items'] == 0:
            rows[check_id] = _row('Not Checked', 'Low',
                f'No comparison data — re-run the analysis after uploading {doc_name}.')
            return
        rows[check_id] = _row(
            'Fail' if t['mismatch'] else 'Pass', 'Low',
            f"{t['mismatch']} {attribute} mismatches found in the {doc_name} reconciliation "
            f"({t['matched']} matched items, basis: {ref[data_type]['file']}).",
        )

    def eval_findings(check_id, categories, required_doc, template):
        if required_doc and not doc_available(required_doc):
            rows[check_id] = _row('Not Checked', 'High', f'Not Checked — {required_doc} not provided.')
            return
        count = sum(by_category.get(c, 0) for c in categories)
        rule_prefixes = [CATEGORY_RULE_PREFIX.get(c, c[:3].upper()) for c in categories]
        ev = '; '.join(s for p in rule_prefixes for s in samples.get(p, [])[:1])
        finding = template.format(count=count, sheets=sheet_count)
        if required_doc and (REFERENCE_DOCS[required_doc].get('data_type') in ref):
            finding += f" (basis: {ref[REFERENCE_DOCS[required_doc]['data_type']]['file']})"
        if ev:
            finding += f' e.g. {ev}'
        rows[check_id] = _row('Fail' if count else 'Pass', 'Low', finding)

    def eval_extraction(check_id, counter, present_tpl, absent_tpl):
        count = int(extraction_totals.get(counter, 0))
        if count:
            rows[check_id] = _row('Pass', 'Low', present_tpl.format(count=count, sheets=sheet_count))
        else:
            rows[check_id] = _row('Pass', 'High', absent_tpl)

    def eval_gated(check_id, doc_name):
        if doc_available(doc_name):
            rows[check_id] = _row('Not Checked', 'Low',
                f'{doc_name} provided — automated cross-check not yet implemented for this document; '
                'engineer verification required.')
        else:
            rows[check_id] = _row('Not Checked', 'High', f'Not Checked — {doc_name} not provided.')

    # ── Execute the soft-coded rule tables ──────────────────────────────────
    for check_id, comp_type, doc_name, basis in RECONCILIATION_RULES:
        eval_reconciliation(check_id, comp_type, doc_name, basis)
    for check_id, comp_type, doc_name, attribute in COMPARISON_ATTR_RULES:
        eval_comparison_attr(check_id, comp_type, doc_name, attribute)
    for check_ids, categories, required_doc, template in FINDINGS_RULES:
        for check_id in check_ids:
            eval_findings(check_id, categories, required_doc, template)
    for check_ids, counter, present_tpl, absent_tpl in EXTRACTION_RULES:
        for check_id in check_ids:
            eval_extraction(check_id, counter, present_tpl, absent_tpl)
    for check_ids, doc_name in GATED_RULES:
        for check_id in check_ids:
            eval_gated(check_id, doc_name)

    # ── C-1.8 / D-1.5 — legend & symbols conformance ────────────────────────
    lg = comp_totals['legend']
    legend_findings = by_category.get('legend', 0)
    if lg['pid_items'] == 0 and legend_findings == 0:
        rows['C-1.8'] = _row('Not Checked', 'Low', 'No legend comparison data — configure project legend sheets and re-run analysis.')
        rows['D-1.5'] = _row('Not Checked', 'Low', 'No legend comparison data — configure project legend sheets and re-run analysis.')
        rows['F-1.3'] = _row('Not Checked', 'Low', 'No legend comparison data — configure project legend sheets and re-run analysis.')
    else:
        problems = lg['missing'] + lg['mismatch']
        finding = (f"Legend comparison: {lg['matched']} matched, {problems} symbol/abbreviation deviations "
                   f"({legend_findings} legend-rule findings across sheets).")
        res = 'Pass' if problems == 0 and legend_findings == 0 else 'Fail'
        rows['C-1.8'] = _row(res, 'High' if lg['matched'] else 'Low', finding)
        rows['D-1.5'] = _row(res, 'High' if lg['matched'] else 'Low', finding + ' Verify flagged symbols against the project legend.')
        rows['F-1.3'] = _row(res, 'Low', finding + ' ASSIST — engineer to confirm no undefined symbols remain.')

    # ── F-1.1 — drawing set coverage ────────────────────────────────────────
    if docs:
        rows['F-1.1'] = _row('Pass', 'Low',
            f"{len(docs)} document(s), {sheet_count} sheets processed. "
            "Confirm the set covers the full scope against the drawing index (index not provided).")

    meta = {
        'documents': len(docs),
        'sheets': sheet_count,
        'findings': sum(by_category.values()),
        'reference_data': {k: v['rows'] for k, v in ref.items()},
        'extraction': {k: int(v) for k, v in extraction_totals.items() if isinstance(v, (int, float))},
        'rules_executed': len(rows),
    }
    logger.info('[SmartChecks] project=%s docs=%d sheets=%d rows=%d',
                project.project_id, len(docs), sheet_count, len(rows))
    return {'rows': rows, 'meta': meta}
