"""Staged SLD analysis pipeline (spec §5) — orchestrates:
files (SldAnalysisFile) -> read SLD (OCR/text) -> compare -> drafting -> result JSON.

Result JSON shape (consumed by SldAnalysisDetailView?tab=…):
{
  headline: {boards, transformers, generators, matched, matchedTotal,
             notOnSldByType, loadConflicts, loadTotal, draftingHigh,
             draftingMedium, draftingTotal, interfaces},
  banner:   {boardMismatch, tagConflict, ratingMismatch, wrongAreaCode},
  equipment: [...],  equipment_meta: {...},
  load: [...],       load_meta: {...},
  drafting: [...],   drafting_meta: {...},
  interfaces: [...], interfaces_meta: {...},
  cables: [...],     cables_meta: {...},
}
"""
from __future__ import annotations

import io
import logging

from ..models import SldAnalysisFile
from . import sld_compare
from .sld_excel import read_equipment_list, read_load_list

logger = logging.getLogger(__name__)


def _file_bytes(sld_file):
    sld_file.file.open('rb')
    data = sld_file.file.read()
    sld_file.file.close()
    return data


def run_analysis(analysis):
    from .sld_extract import extract_tags  # late import: heavy deps
    from .sld_pdf import read_drawing_summary

    analysis.status = 'running'
    analysis.save(update_fields=['status'])

    files = {f.kind: f for f in SldAnalysisFile.objects.filter(project=analysis.project)}
    sld_tags = []
    drawing_summary = {}
    if 'sld' in files:
        pdf_bytes = _file_bytes(files['sld'])
        drawing_summary = read_drawing_summary(pdf_bytes)
        sld_tags = extract_tags(pdf_bytes, analysis=analysis)

    eq_rows, eq_summary = [], {}
    if 'equipment' in files:
        eq_summary = read_equipment_list(io.BytesIO(_file_bytes(files['equipment'])))
        eq_rows = eq_summary['rows']

    load_rows, load_summary = [], {}
    if 'load' in files:
        load_summary = read_load_list(io.BytesIO(_file_bytes(files['load'])))
        load_rows = load_summary['loads']

    # ── comparisons ──────────────────────────────────────────────────────
    eq_result = sld_compare.compare_equipment(sld_tags, eq_rows) if eq_rows else []
    load_result, not_in_list, not_covered = (
        sld_compare.compare_loads(sld_tags, load_rows, eq_rows) if load_rows else ([], [], []))
    eqload_result = sld_compare.compare_equipment_vs_load(eq_rows, load_rows) if (eq_rows and load_rows) else []

    drafting = sld_compare.drafting_tag_checks(sld_tags) if sld_tags else []

    interfaces = [{'tag': t['tag'], 'where': f"Sheet {t['sheet']:03d}",
                   'meaning': 'Other area / drawing reference'}
                  for t in sld_tags if t['category'] == 'interface']
    cables = [{'tag': t['tag'], 'size': t.get('rating', ''), 'sheet': f"Sheet {t['sheet']:03d}"}
              for t in sld_tags if t['category'] == 'cable']

    # ── headline (spec §6.1) ─────────────────────────────────────────────
    boards = sum(1 for t in sld_tags if t['category'] == 'board')
    tfs = sum(1 for t in sld_tags if t['category'] == 'transformer')
    gens = sum(1 for t in sld_tags if t['category'] == 'generator')
    matched = sum(1 for r in eq_result if r['status'] == 'matched')
    new_sheet_rows = [r for r in eq_result if r['listSheet'] == 'New Equipment']
    not_on_sld_by_type = sum(1 for r in new_sheet_rows if r['status'] == 'not-on-sld' and r['reason'])
    eq_total = len(new_sheet_rows)
    load_conflicts = sum(1 for r in load_result if r['status'] in ('board-mismatch', 'rating-mismatch', 'tag-conflict'))
    high = sum(1 for f in drafting if f['severity'] == 'high')
    medium = sum(1 for f in drafting if f['severity'] == 'medium')

    banner = {
        'boardMismatch': sum(1 for r in load_result if r['status'] == 'board-mismatch'),
        'tagConflict': sum(1 for r in load_result if r['status'] == 'tag-conflict'),
        'ratingMismatch': sum(1 for r in load_result if r['status'] == 'rating-mismatch'),
        'wrongAreaCode': sum(1 for f in drafting if f['check'] == 'Wrong area code'),
    }

    analysis.result = {
        'headline': {
            'boards': boards, 'transformers': tfs, 'generators': gens,
            'matched': matched, 'matchedTotal': eq_total,
            'notOnSldByType': not_on_sld_by_type,
            'loadConflicts': load_conflicts, 'loadTotal': len(load_rows),
            'draftingTotal': len(drafting), 'draftingHigh': high, 'draftingMedium': medium,
            'interfaces': len(interfaces),
        },
        'banner': banner,
        'equipment': eq_result,
        'equipment_meta': {
            'summary': {s['name']: s for s in eq_summary.get('sheets', [])},
            'rewrites': eq_summary.get('rewrites', []),
        },
        'load': load_result,
        'load_meta': {
            'board': load_summary.get('board', ''), 'section': load_summary.get('section', ''),
            'notInListCount': len(not_in_list),
            'notCoveredCount': len(not_covered),
            'notes': load_summary.get('notes', []),
        },
        'eqload': eqload_result,
        'eqload_meta': {
            'board': load_summary.get('board', ''), 'section': load_summary.get('section', ''),
        },
        'drafting': drafting,
        'drafting_meta': {'drawing_no': drawing_summary.get('drawing_no', ''),
                          'rev': drawing_summary.get('rev', '')},
        'interfaces': interfaces,
        'cables': cables,
        'drawing': drawing_summary,
    }
    analysis.status = 'completed'
    analysis.save(update_fields=['status', 'result'])
    return analysis.result
