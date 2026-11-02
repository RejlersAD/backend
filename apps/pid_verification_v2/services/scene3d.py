"""
pidchk_scene3d.py — build the 3D scene for one P&ID sheet from the PDF's own vector geometry.

Used by:  GET /api/v2/pid-verification/documents/{id}/scene3d/?page=N   (cache the JSON per document + page)

Inputs
  pdf                 the uploaded PDF as bytes, a file-like object, an open
                      fitz.Document, or a local path (bytes/streams for
                      S3-stored uploads — FileField.path raises there)
  page                0-based sheet index ({"status": "page_out_of_range"} when bad)
  extraction          items for this page from the extraction table:
                        lines:      {id, text, parsed:{size, system, fluid_code, piping_class, insulation, design_cases[]}, bbox:[x0,y0,x1,y1] in % of sheet}
                        equipment:  {id, text, bbox}
                        connectors: {id, text, parsed:{drawing_no, connector_no}, bbox}
  findings            [{id, number, severity, rule_id, title, bbox?:[%], entity_id?}]
  decode              optional callable(line_parsed) -> {cls_desc, ins_desc, service} using the legend register

Output (metres, Y up) — the schema PidScene3D (pid-scene3d.js) renders:
  {status:"ok", preliminary:true, lines:[…], valves:[…], equipment:[…], connectors:[…], findings:[…], bounds:{min,max}}

Rules (all proven on Borouge 13-PP-152-20070 Rev Q, sheet 1: 35 pipe runs, 28 labelled, 8 valves, 5 of 7 findings located):
  1. Pipe strokes: solid lines, width >= 0.4 pt, axis-aligned or 45°, length >= 6 pt, inside the drawing frame
     (exclude the title / notes column: x > 82 % of the sheet width).
  2. Merge collinear strokes; bridge gaps <= 9 pt (valve and reducer symbols break the line).
  3. Drop boxes: two parallel strokes 2.5–9.5 pt apart with >80 % overlap and similar length (connector boxes,
     data-block tables) — never drop coloured (scope) strokes. Drop dimension lines next to "MIN"/"MAX" text.
     Drop short pieces (< 60 pt) that do not touch another pipe.
  4. Equipment outlines: rectangles wider than 150 pt; a vertical pipe whose end stops within 70 pt below such an
     outline is a branch into it -> extend it to the outline ("riser").
  5. Line properties: nearest line-number label of the same orientation (horizontal label sits 0–12 pt above the
     pipe and overlaps its span; vertical label sits 0–12 pt left of the pipe). Unlabelled runs inherit from a
     collinear or touching labelled run.
  6. Valves: two short diagonal strokes (4–14 pt) crossing at their mid-points = bow-tie; snap to the nearest pipe
     within 12 pt.
  7. 3D placement (schematic, not to scale): sheet x -> X, sheet y -> Z (plan), 1 pt = 0.06 m.
     Headers at rack elevation 6.0 m; branches rise to 7.8 m and run into the equipment they feed;
     runs that leave the bottom of the sheet drop to 1.2 m at their end. Red / coloured strokes = new scope.
  8. Findings: anchor = nearest pipe point / valve / equipment to the finding bbox centre (<= 40 pt); findings with
     no bbox and no entity (cause & effect, notes, data blocks) get anchor = null ("Not shown in 3D").
"""
import math, re
import fitz  # PyMuPDF

K = 0.06          # metres per PDF point
RACK, RISE = 6.0, 7.8
SERVICE_BY_FLUID = {  # extend per client profile; defaults for Linde / Borouge
    'BW': 'steam', 'KS': 'steam', 'HS': 'steam', 'HS1': 'steam', 'HS2': 'steam', 'MS': 'steam', 'LS': 'steam', 'DS': 'steam',
    'BA': 'steam', 'HC': 'steam', 'MC': 'steam', 'LC': 'steam',
    'PG': 'process', 'PL': 'process', 'ET': 'process', 'HY': 'process', 'BD': 'process',
    'NG': 'fuel', 'FG': 'fuel', 'UA': 'utility', 'IA': 'utility', 'UN': 'utility', 'CH': 'utility', 'CW': 'utility',
}


def _pct_to_pt(b, W, H):
    return [b[0] * W / 100, b[1] * H / 100, b[2] * W / 100, b[3] * H / 100]


def _seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    l2 = vx * vx + vy * vy or 1
    t = max(0, min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / l2))
    q = (a[0] + vx * t, a[1] + vy * t)
    return math.dist(p, q), q


def _size_in(s):
    s = (s or '').replace(' ', '')
    try:
        return float(s.replace('mm', '')) / 25.4 if 'mm' in s else float(s.replace('"', ''))
    except ValueError:
        return 6.0


def _norm_line_no(text):
    """'1"-68- WG-147263- A2AP01- DA .' → '1"-68-WG-147263-A2AP01-DA'
    (spaces around hyphens and the stray trailing dot removed, 30 Oct 2026)."""
    t = re.sub(r'\s*-\s*', '-', (text or '').strip())
    return t.rstrip(' .')


_LINE_TOKEN_RE = re.compile(
    r'(\d+(?:\.\d+)?")\s*-\s*([A-Z0-9]{1,4})\s*-\s*([A-Z0-9]{1,4})\s*-\s*(\d{3,6})\s*-\s*([A-Z0-9]{2,8})\s*-\s*([A-Z0-9]{1,4})\s*\.?'
)


def clean_finding_text(text):
    """Normalise any line-number tokens inside a finding's display text
    (spec A7) — display and comparison use the same form."""
    return _LINE_TOKEN_RE.sub(lambda m: '-'.join(m.groups()), text or '')


def build_scene(pdf, page, extraction, findings, decode=None):
    """`pdf` may be raw bytes, a file-like object, an open fitz.Document,
    or a local path (bytes/streams are how S3-stored uploads are passed —
    FileField.path raises under remote storage, 30 Oct 2026)."""
    doc = pdf
    if isinstance(pdf, (bytes, bytearray, memoryview)):
        doc = fitz.open(stream=bytes(pdf), filetype='pdf')
    elif hasattr(pdf, 'read'):
        doc = fitz.open(stream=pdf.read(), filetype='pdf')
    elif not isinstance(pdf, fitz.Document):
        doc = fitz.open(pdf)
    if page < 0 or page >= doc.page_count:
        return {'status': 'page_out_of_range', 'pages': doc.page_count}
    P = doc[page]
    W, H = P.rect.width, P.rect.height
    if not P.get_text('text').strip():
        return {'status': 'no_vector_geometry'}
    XL, XR, YT, YB = 0.05 * W, 0.82 * W, 0.012 * H, 0.99 * H
    words = P.get_text('words')
    dims = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words if w[4] in ('MIN', 'MAX', 'MIN.', 'MAX.')]

    # 1) strokes, diagonals, big rectangles
    segs, diag, rects = [], [], []
    hypot = math.hypot
    for g in P.get_drawings():
        w = g.get('width') or 0
        col = g.get('color') or (0, 0, 0)
        coloured = (col[0] > 0.9 and col[1] < 0.2) or (col[1] > 0.9 and col[0] < 0.2)
        if g.get('dashes') not in (None, '', '[] 0'):
            continue
        for it in g['items']:
            if it[0] == 're':
                r = it[1]
                if r.width > 150 and r.height > 20:
                    rects.append([r.x0, r.y0, r.x1, r.y1])
                continue
            if it[0] != 'l':
                continue
            a, b = it[1], it[2]
            ax, ay, bx, by = a.x, a.y, b.x, b.y
            if not (XL < ax < XR and XL < bx < XR and YT < ay < YB and YT < by < YB):
                continue
            dx, dy = abs(ax - bx), abs(ay - by)
            L = hypot(dx, dy)
            if w >= 0.4 and 4 <= L <= 14 and dx > 1 and dy > 1 and abs(dx - dy) / max(dx, dy) < 0.6:
                diag.append((ax, ay, bx, by))
            if w < 0.4 or L < 6:
                continue
            orient = 'h' if dy < 0.6 else 'v' if dx < 0.6 else ('d' if abs(dx - dy) < 1.5 else None)
            if orient:
                segs.append({'a': (ax, ay), 'b': (bx, by), 'w': w, 'ax': orient, 'new': coloured})
    # equipment outlines drawn as 4 separate lines: two long horizontals with matching ends joined by verticals at both ends
    hz = [s for s in segs if s['ax'] == 'h' and abs(s['a'][0] - s['b'][0]) > 150]
    vt = [s for s in segs if s['ax'] == 'v']
    for i, a in enumerate(hz):
        ax0, ax1 = sorted([a['a'][0], a['b'][0]])
        for b in hz[i + 1:]:
            bx0, bx1 = sorted([b['a'][0], b['b'][0]])
            y0, y1 = sorted([a['a'][1], b['a'][1]])
            if abs(ax0 - bx0) < 2 and abs(ax1 - bx1) < 2 and 20 < y1 - y0 < 300:
                def side(x):
                    return any(abs(v['a'][0] - x) < 2 and min(v['a'][1], v['b'][1]) <= y0 + 2 and max(v['a'][1], v['b'][1]) >= y1 - 2 for v in vt)
                if side(ax0) and side(ax1):
                    rects.append([ax0, y0, ax1, y1])
    top_rect = min(rects, key=lambda r: r[1]) if rects else None

    # 2) merge collinear
    def merge(axis):
        groups = {}
        for s in segs:
            if s['ax'] != axis:
                continue
            k = round((s['a'][1] if axis == 'h' else s['a'][0]) * 2) / 2
            lo, hi = sorted([s['a'][0], s['b'][0]] if axis == 'h' else [s['a'][1], s['b'][1]])
            groups.setdefault(k, []).append([lo, hi, s['w'], s['new']])
        out = []
        for k, iv in groups.items():
            iv.sort()
            cur = None
            for lo, hi, w, new in iv:
                if cur and lo <= cur[1] + 9:
                    cur[1] = max(cur[1], hi); cur[2] = max(cur[2], w); cur[3] = cur[3] or new
                else:
                    if cur:
                        out.append((k, *cur))
                    cur = [lo, hi, w, new]
            if cur:
                out.append((k, *cur))
        return out

    pipes = [{'p': [[lo, k], [hi, k]], 'w': w, 'new': new} for k, lo, hi, w, new in merge('h') if hi - lo >= 30]
    pipes += [{'p': [[k, lo], [k, hi]], 'w': w, 'new': new} for k, lo, hi, w, new in merge('v') if hi - lo >= 30]
    pipes += [{'p': [list(s['a']), list(s['b'])], 'w': s['w'], 'new': s['new']} for s in segs if s['ax'] == 'd' and 12 < abs(s['a'][0] - s['b'][0]) < 85]  # long diagonals are cross-out marks

    # 3) drop boxes, dimension lines, isolated short pieces
    def ori(q):
        (x1, y1), (x2, y2) = q['p']
        return 'h' if y1 == y2 else 'v' if x1 == x2 else 'd'

    def span(q):
        (x1, y1), (x2, y2) = q['p']
        return (min(x1, x2), max(x1, x2), y1) if ori(q) == 'h' else (min(y1, y2), max(y1, y2), x1)

    drop = set()
    for i, a in enumerate(pipes):
        if ori(a) == 'd' or a['new']:
            continue
        la, ha, ka = span(a)
        for j in range(i + 1, len(pipes)):
            b = pipes[j]
            if ori(b) != ori(a) or b['new']:
                continue
            lb, hb, kb = span(b)
            ov = min(ha, hb) - max(la, lb)
            short, long_ = min(ha - la, hb - lb), max(ha - la, hb - lb)
            if 2.5 < abs(ka - kb) < 9.5 and ov > 0.8 * short and short > 0.6 * long_:
                drop |= {i, j}
    for i, q in enumerate(pipes):
        if ori(q) == 'd':
            continue
        lo, hi, k = span(q)
        for m in dims:
            if ori(q) == 'v' and abs(m[0] - k) < 12 and lo - 5 < m[1] < hi + 5:
                drop.add(i)
            if ori(q) == 'h' and abs(m[1] - k) < 12 and lo - 5 < m[0] < hi + 5 and hi - lo < 120:
                drop.add(i)
    for i, q in enumerate(pipes):
        (x1, y1), (x2, y2) = q['p']
        for r in rects:
            if (abs(y1 - y2) < 1 and min(abs(y1 - r[1]), abs(y1 - r[3])) < 1.5 and r[0] - 2 <= min(x1, x2) and max(x1, x2) <= r[2] + 2) or \
               (abs(x1 - x2) < 1 and min(abs(x1 - r[0]), abs(x1 - r[2])) < 1.5 and r[1] - 2 <= min(y1, y2) and max(y1, y2) <= r[3] + 2):
                drop.add(i)
    pipes = [q for i, q in enumerate(pipes) if i not in drop]

    def touches(q, others):
        for e in q['p']:
            for o in others:
                if o is not q and _seg_dist(e, *o['p'])[0] < 4:
                    return True
        return False

    pipes = [q for q in pipes if math.dist(*q['p']) >= 60 or touches(q, pipes)]

    # 4) risers into the equipment outline above
    for q in pipes:
        if ori(q) == 'v' and top_rect:
            lo, hi, k = span(q)
            if top_rect[0] < k < top_rect[2] and 0 < lo - top_rect[3] < 70:
                q['p'] = [[k, top_rect[3]], [k, hi]]
                q['riser'] = True

    # 5) line properties from extraction labels
    labels = []
    for it in extraction.get('lines', []):
        if not it.get('bbox'):
            continue
        b = _pct_to_pt(it['bbox'], W, H)
        labels.append({'it': it, 'bb': b, 'vert': (b[3] - b[1]) > (b[2] - b[0])})
    for q in pipes:
        (x1, y1), (x2, y2) = q['p']
        hor = abs(y1 - y2) < 1
        best = None
        for lb in labels:
            x0, y0, xx, yy = lb['bb']
            if hor and not lb['vert'] and min(x1, x2) - 5 <= x0 <= max(x1, x2) and -2 <= y1 - yy <= 12:
                d = y1 - yy
            elif not hor and lb['vert'] and -2 <= x1 - xx <= 12 and min(y1, y2) - 160 <= y0 <= max(y1, y2) + 5:
                d = x1 - xx
            else:
                continue
            if best is None or d < best[0]:
                best = (d, lb['it'])
        q['label'] = best[1] if best else None
    for _ in range(3):
        for q in pipes:
            if q['label']:
                continue
            for o in pipes:
                if o is q or not o['label']:
                    continue
                (x1, y1), (x2, y2) = q['p']; (a, b), (c, d) = o['p']
                collinear = (abs(y1 - y2) < 1 and abs(b - d) < 1 and abs(y1 - b) < 1.2) or (abs(x1 - x2) < 1 and abs(a - c) < 1 and abs(x1 - a) < 1.2)
                if collinear or (touches(q, [o]) and q['new'] == o['new'] and ori(q) == ori(o)):
                    q['label'] = o['label']
                    break

    cx, cz = W / 2, H / 2

    def M(x, y, h):
        return [round((x - cx) * K, 3), round(h, 3), round((y - cz) * K, 3)]

    lines = []
    for i, q in enumerate(pipes):
        (x1, y1), (x2, y2) = q['p']
        it = q['label'] or {}
        pr = it.get('parsed') or {}
        fluid = pr.get('fluid_code')
        dec = decode(pr) if (decode and pr) else {}
        if q.get('riser'):
            yh = max(y1, y2)
            path = [M(x1, yh, RACK), M(x1, yh, RISE), M(x1, min(y1, y2), RISE)]
        elif abs(x1 - x2) < 1 and max(y1, y2) > 0.83 * H:
            top, yb = min(y1, y2), max(y1, y2)
            path = [M(x1, top, RACK), M(x1, yb, RACK), M(x1, yb, 1.2)]
        elif abs(x1 - x2) < 1:
            # North–south (sheet-vertical) runs ride the upper tier (7.0 m)
            # with a riser at each end; east–west runs stay at rack level
            # (6.0 m) — otherwise crossing runs merge into one grid (30 Oct
            # 2026, spec B2).
            top, yb = min(y1, y2), max(y1, y2)
            path = [M(x1, top, RACK), M(x1, top, RISE), M(x1, yb, RISE), M(x1, yb, RACK)]
        else:
            path = [M(x1, y1, RACK), M(x2, y2, RACK)]
        lines.append({
            'id': f'L{i + 1}', 'entity_id': it.get('id'), 'line_no': _norm_line_no(it.get('text') or 'Unlabelled run'),
            'size_in': _size_in(pr.get('size')), 'service': 'h2' if q['new'] else dec.get('service') or SERVICE_BY_FLUID.get(fluid, 'process'),
            'new': bool(q['new']), 'cls': pr.get('piping_class'), 'cls_desc': dec.get('cls_desc'), 'ins': pr.get('insulation'),
            'ins_desc': dec.get('ins_desc'), 'cases': [f"{c.get('no')}: {c.get('p_barg')} barg / {c.get('t_c')} °C{' (STC)' if c.get('stc') else ''}" for c in pr.get('design_cases', [])],
            'system': pr.get('system'), 'fluid': fluid, 'path': path, '_q': q,
        })

    # 6) valves — two short diagonal strokes (4–14 pt) crossing at their
    # mid-points = bow-tie; snap to the nearest pipe within 12 pt.
    # Split by slope sign (a bow-tie needs opposite slopes) and bucket the
    # mid-points — ~O(n). The naive all-pairs scan took minutes on a dense
    # sheet (35k diagonal strokes, 30 Oct 2026).
    pos_grid = {}
    neg = []
    for a in diag:
        m = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
        if (a[3] - a[1]) * (a[2] - a[0]) > 0:
            pos_grid.setdefault((int(m[0] // 3), int(m[1] // 3)), []).append(m)
        else:
            neg.append(m)
    centres = []
    for m2 in neg:
        gx, gy = int(m2[0] // 3), int(m2[1] // 3)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for m1 in pos_grid.get((gx + ddx, gy + ddy), ()):
                    if abs(m1[0] - m2[0]) < 2.5 and abs(m1[1] - m2[1]) < 2.5 \
                            and math.dist(m1, m2) < 2.5:
                        centres.append(((m1[0] + m2[0]) / 2, (m1[1] + m2[1]) / 2))
    # One valve per 4 pt cluster (chain-merge, as the delivered algorithm,
    # with a 4 pt bucket index so dense clusters stay ~O(n)).
    merged = []
    mgrid = {}
    for c in centres:
        gx, gy = int(c[0] // 4), int(c[1] // 4)
        near = [merged[i] for ddx in (-1, 0, 1) for ddy in (-1, 0, 1)
                for i in mgrid.get((gx + ddx, gy + ddy), ())]
        if all(math.dist(c, u) > 4 for u in near):
            mgrid.setdefault((gx, gy), []).append(len(merged))
            merged.append(c)
    centres = merged
    valves = []
    for j, c in enumerate(centres):
        best = None
        for ln in lines:
            d, p = _seg_dist(c, *ln['_q']['p'])
            if best is None or d < best[0]:
                best = (d, ln, p)
        if best and best[0] < 12:
            d, ln, p = best
            q = ln['_q']
            ax = 'x' if abs(q['p'][0][1] - q['p'][1][1]) < 1 else 'z'
            valves.append({'id': f'V{j + 1}', 'pos': M(p[0], p[1], RISE if q.get('riser') else RACK), 'axis': ax, 'size_in': ln['size_in'], 'line_id': ln['id']})

    # equipment: drawn outlines + referenced tags (ghosts at their label)
    equipment = []
    for r in rects:
        txt = ' '.join(w[4] for w in words if r[0] <= w[0] <= r[2] and r[1] <= w[1] <= r[3])
        kind = 'furnace' if 'FURNACE' in txt.upper() else 'vessel'
        equipment.append({'id': f'E{len(equipment) + 1}', 'tag': txt.strip()[:60] or 'Equipment', 'kind': kind,
                          'center': M((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, 0), 'size': [(r[2] - r[0]) * K, 13.0 if kind == 'furnace' else 4.0, (r[3] - r[1]) * K], 'ghost': False})
    code_kind = {'HE': 'exchanger', 'PC': 'pump', 'PA': 'pump', 'PL': 'pump', 'VT': 'column', 'VA': 'column', 'VV': 'vessel', 'FH': 'furnace'}
    size_kind = {'exchanger': [5.2, 1.3, 1.3], 'pump': [2.4, 1.2, 1.2], 'column': [1.6, 8.0, 1.6], 'vessel': [3.0, 3.0, 2.2]}
    for it in extraction.get('equipment', []):
        if not it.get('bbox'):
            continue
        b = _pct_to_pt(it['bbox'], W, H)
        m = re.match(r'\d{2}-([A-Z]{1,3})-', it.get('text', ''))
        kind = code_kind.get(m.group(1) if m else '', 'vessel')
        sz = size_kind.get(kind, [3, 3, 2.2])
        equipment.append({'id': f'E{len(equipment) + 1}', 'entity_id': it.get('id'), 'tag': it['text'], 'kind': kind,
                          'center': M((b[0] + b[2]) / 2 + 20, (b[1] + b[3]) / 2, sz[1] / 2), 'size': sz, 'ghost': True, 'note': 'referenced, not drawn on this sheet'})

    connectors = []
    for it in extraction.get('connectors', []):
        if not it.get('bbox'):
            continue
        b = _pct_to_pt(it['bbox'], W, H)
        if b[0] > 0.2 * W and b[0] < 0.75 * W:
            continue  # only sheet-edge connectors get an arrow
        connectors.append({'id': f'C{len(connectors) + 1}', 'label': (it.get('parsed') or {}).get('service') or it['text'][:24],
                           'ref': (it.get('parsed') or {}).get('drawing_no') or '', 'pos': M(b[2] + 4, (b[1] + b[3]) / 2, RACK), 'dir': 'in' if b[0] < W / 2 else 'out'})

    # 8) findings
    by_entity = {ln['entity_id']: ln for ln in lines if ln['entity_id']}
    # entity → any extraction item (its own bbox sits on/near its pipe —
    # catches findings whose labelled pipe missed the label match).
    item_by_id = {}
    for kind_items in extraction.values():
        for it in kind_items or []:
            if it.get('id') is not None:
                item_by_id[it['id']] = it
    out_f = []
    for n, f in enumerate(findings, 1):
        anchor, ref = None, None
        fbbox = f.get('bbox')
        if not fbbox and f.get('entity_id') in item_by_id:
            fbbox = item_by_id[f['entity_id']].get('bbox')
        if f.get('entity_id') in by_entity:
            ln = by_entity[f['entity_id']]; p = ln['path'][len(ln['path']) // 2]; anchor, ref = p, ln['id']
        elif fbbox:
            b = _pct_to_pt(fbbox, W, H); c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            best = None
            for ln in lines:
                d, p = _seg_dist(c, *ln['_q']['p'])
                if best is None or d < best[0]:
                    best = (d, ln, p)
            for e in equipment:
                ex, ez = e['center'][0] / K + cx, e['center'][2] / K + cz
                d = math.dist(c, (ex, ez))
                if best is None or d < best[0]:
                    best = (d, e, None)
            if best and best[0] <= 40:
                d, obj, p = best
                if p is None:
                    anchor, ref = [obj['center'][0], obj['size'][1] + 0.6, obj['center'][2]], obj['id']
                else:
                    q = obj['_q']; anchor, ref = M(p[0], p[1], RISE if q.get('riser') else RACK), obj['id']
        out_f.append({'id': f.get('id') or f'F{n}', 'n': f.get('number') or n, 'label': f.get('display_id'), 'rule': f.get('rule_id'), 'title': f.get('title'),
                      'kind': {'critical': 'error', 'major': 'error', 'error': 'error', 'minor': 'minor'}.get((f.get('severity') or '').lower(), 'query'),
                      'anchor': anchor, 'ref': ref})

    for ln in lines:
        ln.pop('_q', None)
    pts = [p for ln in lines for p in ln['path']] or [[0, 0, 0]]
    return {'status': 'ok', 'units': 'm', 'preliminary': True, 'page': page,
            'lines': lines, 'valves': valves, 'equipment': equipment, 'connectors': connectors, 'findings': out_f,
            'bounds': {'min': [min(p[0] for p in pts), 0, min(p[2] for p in pts)], 'max': [max(p[0] for p in pts), 14, max(p[2] for p in pts)]}}
