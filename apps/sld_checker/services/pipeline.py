"""
SLD Checker pipeline — run(analysis_id)
========================================

Reads the core analysis (apps.electrical_comparison SldAnalysis.result +
the project's SLD file) and rebuilds the checker's own rows
(sldc_sheets / sldc_nodes / sldc_edges / sldc_findings) for that analysis.

Guarantees:
- IDEMPOTENT: deletes and rebuilds only sldc_* rows for the analysis.
- NEVER writes to electrical_comparison tables (core models are imported
  read-only; the core result JSON is only read).
- Never invents data: no tag located → bbox=None; empty title block →
  empty drawing_no (the API serializer shows "Drawing number to confirm").
"""
from __future__ import annotations

import io
import logging
import re

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.electrical_comparison.models import SldAnalysis, SldAnalysisFile

from ..models import SldCheckConfig, SldEdge, SldFinding, SldNode, SldSheet
from . import ratings as ratings_mod
from .rules import Ctx, run_rules
from .checklist_map import RULE_TO_ITEMS

logger = logging.getLogger(__name__)

DPI = 150
CABLE_TAG_RE = re.compile(r'-(PHV|P)-\d{3}$')

# extractor category -> node kind
CATEGORY_KIND = {
    'board': 'board', 'board-u': 'board', 'transformer': 'transformer',
    'generator': 'generator', 'subpanel': 'subpanel', 'motor': 'motor',
    'load': 'load', 'interface': 'interface',
}

FAMILY_PREFIX = {'engineering': 'E', 'load-list': 'L', 'drafting': 'D',
                 'equipment': 'Q', 'cross': 'Q'}
SEV_ORDER = {'critical': 0, 'major': 1, 'minor': 2}


def _norm(value):
    from apps.electrical_comparison.services.sld_extract import norm_tag
    return norm_tag(value)


def _guess_kind(tag):
    t = tag.upper()
    if '-TF-' in t:
        return 'transformer'
    if '-GD-' in t:
        return 'generator'
    if '-SB-' in t or '-DB-' in t or re.match(r'^\d{3}-U-\d{3}', t):
        return 'board'
    if re.search(r'-(PM|UM|NM|NPM|KM|RM)-', t):
        return 'motor'
    if re.match(r'^[LP]-?\d', t) or re.match(r'^[LP]-[A-Z]*\d', t):
        return 'subpanel'
    return 'load'


def _file_bytes(sld_file):
    """Read a stored file through the storage API — never .path."""
    sld_file.file.open('rb')
    try:
        return sld_file.file.read()
    finally:
        sld_file.file.close()


def _wipe(analysis_id):
    for model in (SldFinding, SldEdge, SldNode, SldSheet):
        model.objects.filter(analysis_id=analysis_id).delete()


def run(analysis_id):
    analysis = SldAnalysis.objects.get(id=analysis_id)
    result = analysis.result or {}
    project = analysis.project
    config, _ = SldCheckConfig.objects.get_or_create(project=project)

    _wipe(analysis_id)

    sld_file = SldAnalysisFile.objects.filter(project=project, kind='sld').first()
    drawing = result.get('drawing') or {}
    pdf_type = drawing.get('pdf_type') or 'scan'
    readability = {'vector-text': 'text', 'vector-outlines': 'outline-ocr'}.get(pdf_type, 'scan')

    pdf_bytes = None
    doc = None
    if sld_file is not None:
        try:
            pdf_bytes = _file_bytes(sld_file)
            import pymupdf
            doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
        except Exception:
            logger.exception('[SLDC] could not open the SLD file for %s', analysis_id)
            doc = None

    # ── 2. sheets ────────────────────────────────────────────────────────
    sheet_meta = {s.get('page'): s for s in (drawing.get('sheets') or [])}
    sheets = {}   # 1-based page -> SldSheet
    if doc is not None:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(DPI / 72.0, DPI / 72.0))
            key = f'sld_checker/{analysis_id}/page_{i + 1}.png'
            default_storage.delete(key)
            key = default_storage.save(key, ContentFile(pix.tobytes('png')))
            meta = sheet_meta.get(i + 1, {})
            sheets[i + 1] = SldSheet.objects.create(
                analysis_id=analysis_id,
                file_id=sld_file.id if sld_file else None,
                page_index=i,
                drawing_no=meta.get('drawing_no') or '',
                rev=meta.get('rev') or '',
                title=drawing.get('title') or '',
                readability=readability,
                image_key=key,
            )

    # ── 3. locate tags (core extractor — imported, not copied) ──────────
    tags = []
    if pdf_bytes:
        try:
            from apps.electrical_comparison.services.sld_extract import extract_tags
            tags = extract_tags(pdf_bytes) or []
        except Exception:
            logger.exception('[SLDC] extract_tags failed for %s', analysis_id)
            tags = []

    # percent bbox ±1.5% around the extracted point (never faked)
    page_sizes = {}
    if doc is not None:
        page_sizes = {i: (doc.load_page(i).rect.width, doc.load_page(i).rect.height)
                      for i in range(doc.page_count)}

    def bbox_pct(hit):
        if hit.get('width') and hit.get('height') and hit['sheet'] - 1 in page_sizes:
            w, h = page_sizes[hit['sheet'] - 1]
            return [hit['x'] / w * 100, hit['y'] / h * 100,
                    (hit['x'] + hit['width']) / w * 100, (hit['y'] + hit['height']) / h * 100]
        if hit['sheet'] - 1 in page_sizes:
            w, h = page_sizes[hit['sheet'] - 1]
            return [max(0.0, hit['x'] / w * 100 - 1.5), max(0.0, hit['y'] / h * 100 - 1.5),
                    min(100.0, hit['x'] / w * 100 + 1.5), min(100.0, hit['y'] / h * 100 + 1.5)]
        return None

    extracted = {}   # norm_tag -> hit (first occurrence)
    for t in tags:
        n = _norm(t.get('tag', ''))
        if n and n not in extracted:
            extracted[n] = t

    # tags expected from the core result rows
    core_rows = []   # (norm, display_tag, core_ref, row)
    for ref, rows in (('equipment', result.get('equipment') or []),
                      ('load', result.get('load') or []),
                      ('eqload', result.get('eqload') or []),
                      ('drafting', result.get('drafting') or []),
                      ('interfaces', result.get('interfaces') or []),
                      ('cables', result.get('cables') or [])):
        for row in rows:
            tag = (row.get('tag') or '').strip()
            n = _norm(tag)
            if n and all(n != c[0] for c in core_rows):
                core_rows.append((n, tag, ref, row))

    # ── nodes ────────────────────────────────────────────────────────────
    nodes = {}   # norm -> SldNode
    # every extracted tag (cables/placeholders excluded) is a node
    for n, hit in extracted.items():
        if hit.get('category') in ('cable', 'placeholder'):
            continue
        kind = CATEGORY_KIND.get(hit.get('category'), _guess_kind(n))
        nodes[n] = SldNode.objects.create(
            analysis_id=analysis_id, sheet=sheets.get(hit.get('sheet')),
            tag=hit.get('tag') or n, norm_tag=n, kind=kind,
            description=hit.get('description') or '',
            bbox=bbox_pct(hit), confidence=hit.get('_conf'),
            source='core', core_ref='',
        )
    # core rows whose tag was not extracted (bbox stays None — never faked)
    for n, tag, ref, row in core_rows:
        hit = extracted.get(n)
        if n in nodes:
            node = nodes[n]
            if node.core_ref != ref:
                node.core_ref = ref
                node.save(update_fields=['core_ref'])
            if ref == 'load' and row.get('kwList'):
                node.kw = float(row['kwList'])
                node.confidence = 1.0
                node.save(update_fields=['kw', 'confidence'])
            if not node.description and row.get('description'):
                node.description = row['description']
                node.save(update_fields=['description'])
            continue
        if ref == 'cables':
            continue  # cable rows are edge data, not standalone nodes
        kind = _guess_kind(n)
        sheet = None
        node = SldNode.objects.create(
            analysis_id=analysis_id, sheet=sheet, tag=(hit or {}).get('tag') or tag,
            norm_tag=n, kind=kind,
            description=(row.get('description') or (hit or {}).get('description') or ''),
            bbox=bbox_pct(hit) if hit else None,
            confidence=((hit or {}).get('_conf')),
            source='core', core_ref=ref,
        )
        # authoritative list values (load list kW) — full confidence
        if ref == 'load' and row.get('kwList'):
            node.kw = float(row['kwList'])
            node.confidence = 1.0
            node.save(update_fields=['kw', 'confidence'])
        nodes[n] = node

    # ── 4. additive ratings (source='checker') ──────────────────────────
    png_local = {}
    for page_no, sheet in sheets.items():
        try:
            png_local[page_no] = default_storage.path(sheet.image_key)
        except Exception:
            png_local[page_no] = None

    for n, node in nodes.items():
        hit = extracted.get(n)
        if not hit or doc is None or hit['sheet'] - 1 >= doc.page_count:
            continue
        hit = dict(hit, bbox_pct=node.bbox)
        page = doc.load_page(hit['sheet'] - 1)
        fields, conf, block = ratings_mod.extract_node_ratings(
            page, hit, node.kind, png_path=png_local.get(hit['sheet']))
        if not fields:
            continue
        changed = []
        for f, v in fields.items():
            if f == 'ratio':
                continue  # kept implicitly via voltage_v
            # checker values are additive: never overwrite a core (list) value
            if getattr(node, f) in (None, ''):
                setattr(node, f, v)
                changed.append(f)
        if changed:
            if node.confidence is None:
                node.confidence = conf
            node.needs_review = conf < config.conf_min
            node.source = 'checker' if node.core_ref == '' else node.source
            node.save(update_fields=changed + ['confidence', 'needs_review', 'source'])

    # ── 5. topology edges ───────────────────────────────────────────────
    edges = _build_edges(analysis_id, nodes, extracted, sheets, doc, png_local)

    # interlock evidence: board tag and 'interlock' in the same page text
    interlock_boards = set()
    if doc is not None:
        for i in range(doc.page_count):
            try:
                text = _norm(doc.load_page(i).get_text('text') or '')
            except Exception:
                continue
            if 'INTERLOCK' in text:
                for n, node in nodes.items():
                    if node.kind == 'board' and n in text:
                        interlock_boards.add(n)

    # ── 6. rules ─────────────────────────────────────────────────────────
    ctx = Ctx(analysis_id=str(analysis_id), config=config,
              nodes=list(nodes.values()), edges=edges,
              interlock_boards=interlock_boards)
    finding_dicts = run_rules(ctx)

    # ── 7. core → finding mapping (map, never recompute) ────────────────
    finding_dicts += map_core_findings(result, nodes)

    # ── numbering & save ─────────────────────────────────────────────────
    _save_findings(analysis_id, finding_dicts)

    if doc is not None:
        doc.close()
    return {
        'sheets': len(sheets), 'nodes': len(nodes), 'edges': len(edges),
        'findings': SldFinding.objects.filter(analysis_id=analysis_id).count(),
    }


def _build_edges(analysis_id, nodes, extracted, sheets, doc, png_local):
    edges = []

    def add_edge(src, dst, kind, hit=None, fdata=None):
        if src is None or dst is None or src.id == dst.id:
            return None
        fdata = fdata or {}
        e = SldEdge.objects.create(
            analysis_id=analysis_id, from_node=src, to_node=dst, kind=kind,
            feeder_no=fdata.get('feeder_no', ''), device=fdata.get('device', ''),
            device_a=fdata.get('device_a'), device_ka=fdata.get('device_ka'),
            starter=fdata.get('starter'), relays=fdata.get('relays', []),
            sheet=sheets.get((hit or {}).get('sheet')) if hit else None,
            bbox=None,
        )
        edges.append(e)
        return e

    # loads fed from boards (extractor fedFrom / core boardSld)
    for n, node in nodes.items():
        hit = extracted.get(n)
        fed = (hit or {}).get('fedFrom') or ''
        if not fed:
            continue
        boards = [b for b in re.split(r'[/,]', fed) if _norm(b) in nodes]
        for i, b in enumerate(boards):
            board = nodes[_norm(b)]
            if board.kind not in ('board', 'subpanel'):
                continue
            if node.kind in ('transformer', 'generator', 'source'):
                continue
            if node.kind in ('board', 'subpanel'):
                kind = 'incomer'
            else:
                kind = 'feeder' if i == 0 else 'alternative'
            fdata = {}
            if doc is not None and hit and hit['sheet'] - 1 < doc.page_count:
                try:
                    txt = ratings_mod._words_near(doc.load_page(hit['sheet'] - 1),
                                                  hit['x'], hit['y'])
                    fdata = ratings_mod.parse_feeder(txt)
                except Exception:
                    fdata = {}
            add_edge(board, node, kind, hit=hit, fdata=fdata)

    # transformer / generator incomers: transformer with fedFrom to a board,
    # else transformer→first board on the same sheet is NOT assumed (never fake)
    for n, node in nodes.items():
        if node.kind not in ('transformer', 'generator'):
            continue
        hit = extracted.get(n)
        fed = _norm((hit or {}).get('fedFrom') or (hit or {}).get('bus') or '')
        if fed and fed in nodes and nodes[fed].kind in ('board', 'subpanel'):
            add_edge(node, nodes[fed], 'incomer', hit=hit)

    # ties between board sections: same base, different trailing letters
    boards = [nd for nd in nodes.values() if nd.kind == 'board']
    by_base = {}
    for b in boards:
        m = re.match(r'^(.*?)([A-Z])$', b.norm_tag)
        if m:
            by_base.setdefault(m.group(1), []).append(b)
    tied = set()
    for base, members in by_base.items():
        members.sort(key=lambda x: x.norm_tag)
        for a, b in zip(members, members[1:]):
            pair = (a.id, b.id)
            if pair in tied:
                continue
            tied.add(pair)
            add_edge(a, b, 'tie')
    return edges


# ── core → finding mapping ────────────────────────────────────────────────
LOAD_STATUS_MAP = {
    'board-mismatch': ('CMP-LL-BRD', 'major'),
    'rating-mismatch': ('CMP-LL-KW', 'major'),
    'tag-conflict': ('CMP-LL-TAG', 'critical'),
}


def map_drafting_row(row, nodes=None):
    """One core drafting row → finding dict. Cable-shaped tags are a
    possible OCR misread of a cable tag → downgraded to a Query."""
    check = row.get('check', '')
    slug = re.sub(r'[^A-Z0-9]+', '-', check.upper()).strip('-')
    tag = row.get('tag', '')
    severity = {'high': 'critical', 'medium': 'major'}.get(row.get('severity'), 'minor')
    result = 'Non-Conformance'
    note = ''
    if CABLE_TAG_RE.search(_norm(tag)):
        result = 'Query'
        severity = 'minor'
        note = 'Possible OCR misread of a cable tag. '
    node = (nodes or {}).get(_norm(tag))
    return {
        'rule_id': f'DRF-{slug}', 'family': 'drafting', 'severity': severity,
        'result': result,
        'title': f'{check} — {tag}',
        'found': note + (row.get('detail') or f'{check}: {tag}'),
        'node_ids': [str(node.id)] if node else [],
        'sheet': node.sheet if node else None,
        'bbox': node.bbox if node else None,
        'checklist_items': RULE_TO_ITEMS.get(f'DRF-{slug}', []),
        'core_ref': 'drafting',
    }


def map_core_findings(result, nodes):
    """Core comparison rows → findings (mapped, never recomputed).
    not-on-sld / sld-demolition / no-tag-in-list / eqload / interfaces /
    cables are NOT findings."""
    out = []
    for row in result.get('load') or []:
        mapping = LOAD_STATUS_MAP.get(row.get('status'))
        if not mapping:
            continue
        rule_id, sev = mapping
        node = nodes.get(_norm(row.get('tag', '')))
        out.append({
            'rule_id': rule_id, 'family': 'load-list', 'severity': sev,
            'result': 'Non-Conformance',
            'title': f"Load list vs SLD: {row['status']} — {row.get('tag', '')}",
            'found': row.get('issue') or row.get('status', ''),
            'node_ids': [str(node.id)] if node else [],
            'sheet': node.sheet if node else None,
            'bbox': node.bbox if node else None,
            'checklist_items': RULE_TO_ITEMS.get(rule_id, []),
            'core_ref': 'load',
        })
    for row in result.get('drafting') or []:
        out.append(map_drafting_row(row, nodes))
    for row in result.get('equipment') or []:
        if row.get('status') != 'sld-only':
            continue
        node = nodes.get(_norm(row.get('tag', '')))
        out.append({
            'rule_id': 'CMP-EL-SLD', 'family': 'equipment', 'severity': 'major',
            'result': 'Query',
            'title': f"On SLD but not in the equipment list — {row.get('tag', '')}",
            'found': row.get('description') or 'sld-only',
            'node_ids': [str(node.id)] if node else [],
            'sheet': node.sheet if node else None,
            'bbox': node.bbox if node else None,
            'checklist_items': RULE_TO_ITEMS.get('CMP-EL-SLD', []),
            'core_ref': 'equipment',
        })
    return out


def _save_findings(analysis_id, finding_dicts):
    """Severity-sort then number per family prefix (E1…, L1…, D1…, Q1…)."""
    finding_dicts.sort(key=lambda f: (SEV_ORDER.get(f['severity'], 9),
                                      f['family'], f['rule_id'], f['title']))
    counters = {}
    for f in finding_dicts:
        prefix = FAMILY_PREFIX.get(f['family'], 'Q')
        counters[prefix] = counters.get(prefix, 0) + 1
        f['number'] = f'{prefix}{counters[prefix]}'
        SldFinding.objects.create(analysis_id=analysis_id, **f)
