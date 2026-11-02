"""
Cross-drawing checks — P&ID Verification V2 (spec A2)
=====================================================

Run after every upload, re-run or delete in the project, over the CURRENT
sheets only (is_drawing=True, not superseded). Results are stored as
PIDVCrossFinding rows numbered X1…Xn; the sheet-to-sheet link map and the
not-uploaded reference list live on project.metadata ('cross_links' /
'external_refs' / 'cross_stats') for the review endpoint and the report.

Rules:
  XCON-001  connector on sheet A points to uploaded sheet B — B must have
            a connector back to A (same connector number when both carry
            one, same service, opposite direction). Otherwise:
            "Connector has no return on the other sheet" (query).
  XLINE-001 same line number (system-fluid-serial) on several sheets must
            have the same size, piping class and insulation. Different size
            AND class → error; otherwise query.
  XTAG-001  same equipment/instrument tag on several sheets with a
            different type (query).
  XREV-001  linked sheets whose revisions differ by more than one issue
            (minor).

References to drawings that are NOT uploaded never create findings — they
are stored in external_refs for the report and checklist 1.11 (Engineer).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional

from .sheets import _rev_key

logger = logging.getLogger(__name__)

_PKG_SUFFIX_RE = re.compile(r'-P\d+$')  # Borouge package suffix: 13-PP-152-20070-P2


def _norm(v) -> str:
    return str(v or '').strip().upper()


def _norm_drawing_no(v) -> str:
    """Drawing-number matching key: case/space-insensitive, package suffix
    (-P2) stripped — connectors reference the bare number while title
    blocks carry the package (30 Oct 2026: 933 false 'not uploaded' refs)."""
    return _PKG_SUFFIX_RE.sub('', _norm(v))


def run_cross_checks(project) -> Dict:
    """Recompute all cross-drawing findings + links for a project.
    Returns the stats dict (also stored on project.metadata)."""
    from apps.pid_verification_v2.models import (
        PIDVCrossFinding, PIDVExtractionItem, PIDVSheet,
    )

    sheets = list(
        PIDVSheet.objects
        .filter(project=project, is_drawing=True, superseded_by__isnull=True)
        .select_related('document')
    )
    by_no: Dict[str, object] = {}
    for s in sheets:
        if s.drawing_no:
            by_no.setdefault(_norm_drawing_no(s.drawing_no), s)
    by_doc_page = {(s.document_id, s.page_index): s for s in sheets}
    doc_ids = {s.document_id for s in sheets}

    findings_out: List[dict] = []

    if not doc_ids:
        PIDVCrossFinding.objects.filter(project=project).delete()
        _store_meta(project, [], [], [])
        return _stats(sheets, [], [], [], [])

    items = list(
        PIDVExtractionItem.objects
        .filter(document_id__in=doc_ids)
        .values('id', 'document_id', 'page', 'kind', 'text', 'parsed', 'bbox')
    )

    def sheet_of(it) -> Optional[object]:
        return by_doc_page.get((it['document_id'], it['page']))

    def ev(it, sheet) -> dict:
        return {'sheet_id': sheet.pk, 'entity_id': it['id'], 'bbox': it.get('bbox')}

    # ── connectors → links, XCON-001, external_refs ──────────────────────
    connectors = []
    for it in items:
        if it['kind'] != 'connectors':
            continue
        p = it.get('parsed') or {}
        if p.get('connector_type') != 'drawing_reference' and not p.get('drawing_no'):
            continue
        sheet = sheet_of(it)
        target_no = _norm_drawing_no(p.get('drawing_no'))
        if not sheet or not target_no:
            continue
        connectors.append({'sheet': sheet, 'item': it, 'target': target_no,
                           'no': _norm(p.get('connector_no')), 'service': _norm(p.get('service')),
                           'direction': _norm(p.get('direction'))})

    links: Dict[tuple, dict] = {}
    for c in connectors:
        tgt = by_no.get(c['target'])
        if tgt and tgt.pk != c['sheet'].pk:
            key = (c['sheet'].pk, tgt.pk)
            links.setdefault(key, {'a': c['sheet'], 'b': tgt, 'connectors': []})['connectors'].append(c)

    # both-ways flag
    link_list = []
    seen_pairs = set()
    for key, l in links.items():
        reverse = (key[1], key[0]) in links
        pair = tuple(sorted(key))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        link_list.append({
            'a': {'sheet_id': l['a'].pk, 'drawing_no': l['a'].drawing_no},
            'b': {'sheet_id': l['b'].pk, 'drawing_no': l['b'].drawing_no},
            'both': reverse,
        })

    for c in connectors:
        tgt = by_no.get(c['target'])
        if not tgt:
            continue  # not uploaded → external_refs, never a finding
        returns = [r for r in connectors
                   if r['sheet'].pk == tgt.pk and r['target'] == _norm_drawing_no(c['sheet'].drawing_no)]
        bad = None
        if not returns:
            bad = 'no return connector'
        else:
            r = returns[0]
            if c['no'] and r['no'] and c['no'] != r['no']:
                bad = f"return connector number {r['no']} ≠ {c['no']}"
            elif c['service'] and r['service'] and c['service'] != r['service']:
                bad = 'return connector carries a different service'
            elif (c['direction'] and r['direction'] and c['direction'] == r['direction']
                  and c['direction'] in ('IN', 'OUT')):
                bad = 'return connector points the same way'
        if bad:
            findings_out.append({
                'code': 'XCON-001', 'kind': 'query',
                'title': 'Connector has no return on the other sheet',
                'detail': (f"{c['sheet'].drawing_no} → {c['target']} ({bad}). "
                           f"Service {c['service'] or '—'} · connector {c['no'] or '—'}."),
                'sheets': [c['sheet'].pk, tgt.pk],
                'evidence': [ev(c['item'], c['sheet'])],
            })

    external_map: Dict[tuple, int] = defaultdict(int)
    for c in connectors:
        if c['target'] not in by_no:
            external_map[(c['sheet'].pk, c['target'])] += 1
    external_refs = [
        {'sheet_id': sid, 'drawing_no': no, 'count': n}
        for (sid, no), n in sorted(external_map.items())
    ]

    # ── XLINE-001: same line number on several sheets ────────────────────
    line_groups: Dict[tuple, list] = defaultdict(list)
    for it in items:
        if it['kind'] != 'lines':
            continue
        p = it.get('parsed') or {}
        key = (_norm(p.get('system')), _norm(p.get('fluid_code')), _norm(p.get('serial')))
        if not all(key):
            continue
        sheet = sheet_of(it)
        if sheet:
            line_groups[key].append((sheet, it, p))
    for (system, fluid, serial), rows in sorted(line_groups.items()):
        sheet_ids = {s.pk for s, _, _ in rows}
        if len(sheet_ids) < 2:
            continue
        sizes = {_norm(p.get('size')).rstrip('"') for _, _, p in rows if p.get('size')}
        classes = {_norm(p.get('piping_class')) for _, _, p in rows if p.get('piping_class')}
        insuls = {_norm(p.get('insulation')) for _, _, p in rows if p.get('insulation')}
        if len(sizes) <= 1 and len(classes) <= 1 and len(insuls) <= 1:
            continue
        line_no = rows[0][1]['text'].strip()
        diff_size, diff_class = len(sizes) > 1, len(classes) > 1
        kind = 'error' if (diff_size and diff_class) else 'query'
        what = 'size and class' if (diff_size and diff_class) else 'size' if diff_size else 'class' if diff_class else 'insulation'
        involved = sorted({s for s, _, _ in rows}, key=lambda s: s.pk)
        findings_out.append({
            'code': 'XLINE-001', 'kind': kind,
            'title': f'Same line number, different {what}',
            'detail': (f'{line_no} appears on {len(involved)} sheets with '
                       f'{" / ".join(sorted(sizes)) or "—"} size · '
                       f'{" / ".join(sorted(classes)) or "—"} class · '
                       f'{" / ".join(sorted(insuls)) or "—"} insulation. '
                       'Acceptable only when a spec break or reducer is shown at the sheet boundary.'),
            'sheets': [s.pk for s in involved],
            'evidence': [ev(it, s) for s, it, _ in rows],
        })

    # ── XTAG-001: same tag, different type ───────────────────────────────
    tag_groups: Dict[str, list] = defaultdict(list)
    for it in items:
        if it['kind'] not in ('equipment', 'instruments'):
            continue
        p = it.get('parsed') or {}
        tag = _norm(p.get('tag') or it['text'])
        typ = _norm(p.get('code') or p.get('function_letters'))
        if not tag:
            continue
        sheet = sheet_of(it)
        if sheet:
            tag_groups[tag].append((sheet, it, typ))
    for tag, rows in sorted(tag_groups.items()):
        sheet_ids = {s.pk for s, _, _ in rows}
        types = {t for _, _, t in rows if t}
        if len(sheet_ids) < 2 or len(types) < 2:
            continue
        involved = sorted({s for s, _, _ in rows}, key=lambda s: s.pk)
        findings_out.append({
            'code': 'XTAG-001', 'kind': 'query',
            'title': f"Tag {tag} has different types across sheets",
            'detail': f'{tag} appears on {len(involved)} sheets as {" / ".join(sorted(types))}.',
            'sheets': [s.pk for s in involved],
            'evidence': [ev(it, s) for s, it, _ in rows],
        })

    # ── XREV-001: revision drift across linked sheets ────────────────────
    for l in link_list:
        a = next(s for s in sheets if s.pk == l['a']['sheet_id'])
        b = next(s for s in sheets if s.pk == l['b']['sheet_id'])
        ra, rb = _rev_key(a.rev), _rev_key(b.rev)
        if ra is None or rb is None or ra[0] != rb[0]:
            continue
        if ra[0] == 0 and abs(len(ra[2]) - len(rb[2])) == 0 and ra[2] and rb[2]:
            dist = abs(sum(map(ord, ra[2])) - sum(map(ord, rb[2])))
        elif ra[0] == 1:
            dist = abs(ra[1] - rb[1])
        else:
            continue
        if dist > 1:
            findings_out.append({
                'code': 'XREV-001', 'kind': 'minor',
                'title': f'Linked sheets at different revisions ({a.rev or "—"} vs {b.rev or "—"})',
                'detail': f'{a.drawing_no} Rev {a.rev or "—"} is linked to {b.drawing_no} Rev {b.rev or "—"} — revisions differ by more than one issue.',
                'sheets': [a.pk, b.pk],
                'evidence': [],
            })

    # ── persist ──────────────────────────────────────────────────────────
    PIDVCrossFinding.objects.filter(project=project).delete()
    PIDVCrossFinding.objects.bulk_create([
        PIDVCrossFinding(
            project=project, number=i + 1, code=f['code'], kind=f['kind'],
            title=f['title'], detail=f['detail'], sheets=f['sheets'], evidence=f['evidence'],
        )
        for i, f in enumerate(findings_out)
    ], batch_size=500)

    _store_meta(project, link_list, external_refs, findings_out)
    stats = _stats(sheets, link_list, findings_out, external_refs, items)
    logger.info(
        '[CrossChecks] project=%s: %d sheets, %d links, %d cross findings, %d external refs',
        project.project_id, len(sheets), len(link_list), len(findings_out), len(external_refs),
    )
    return stats


def _store_meta(project, links, external_refs, findings_out) -> None:
    try:
        md = dict(project.metadata or {})
        md['cross_links'] = links
        md['external_refs'] = external_refs
        md['cross_stats'] = {
            'links': len(links),
            'links_both': sum(1 for l in links if l['both']),
            'links_one_way': sum(1 for l in links if not l['both']),
            'cross_findings': len(findings_out),
            'by_code': {c: sum(1 for f in findings_out if f['code'] == c)
                        for c in ('XCON-001', 'XLINE-001', 'XTAG-001', 'XREV-001')},
        }
        project.metadata = md
        project.save(update_fields=['metadata', 'updated_at'])
    except Exception:
        logger.warning('[CrossChecks] metadata store failed (non-fatal)', exc_info=True)


def _stats(sheets, links, findings, external_refs, items) -> Dict:
    from apps.pid_verification_v2.models import PIDVFinding
    doc_ids = {s.document_id for s in sheets}
    multi = defaultdict(set)
    for it in items:
        if it['kind'] != 'lines':
            continue
        p = it.get('parsed') or {}
        key = (_norm(p.get('system')), _norm(p.get('fluid_code')), _norm(p.get('serial')))
        if all(key):
            multi[key].add((it['document_id'], it['page']))
    lines_multi = sum(1 for v in multi.values() if len(v) > 1)
    return {
        'sheets': len(sheets),
        'files': len(doc_ids),
        'sheet_findings': PIDVFinding.objects.filter(drawing__document_id__in=doc_ids).count() if doc_ids else 0,
        'links': len(links),
        'links_both': sum(1 for l in links if l['both']),
        'links_one_way': sum(1 for l in links if not l['both']),
        'lines_multi': lines_multi,
        'lines_conflict': sum(1 for f in findings if f['code'] == 'XLINE-001'),
        'external_refs': len(external_refs),
    }
