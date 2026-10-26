"""§6.2 — equipment, pipes, straight runs, line labels and label anchoring.

Pipe pen is a project setting (`pipe_pen`, default 'red'): on the reference
sheets new piping is RED 0.48 pt; black 0.48 pt is tables, leaders and
existing plant. Leaders (black 0.48 pt) are followed from a label block to
the pipe run it points at.
"""
import re
from dataclasses import dataclass, field

from . import settings as S

_EQUIP_RE = re.compile(S.EQUIP_LABEL_RE)
_LABEL_RE = re.compile(S.LINE_LABEL_RE)

_RED = 0xFF0000


@dataclass
class Run:
    """A merged collinear straight pipe run."""
    axis: str                 # 'h' | 'v'
    fixed: float              # y for horizontal, x for vertical
    start: float
    end: float
    page: int
    links: list = field(default_factory=list)   # indices of linked runs

    def covers(self, pos):
        return self.start - 0.5 <= pos <= self.end + 0.5

    @property
    def length(self):
        return self.end - self.start


@dataclass
class Equipment:
    tag: str
    page: int
    label_bbox: tuple
    body_bbox: tuple
    name: str = ''              # equipment data box text


@dataclass
class LineLabel:
    line: str                 # normalised line number
    size: str
    unit: str
    service_code: str
    serial: str
    pipe_class: str
    insulation: str
    page: int
    bbox: tuple               # label block bbox (label + condition rows)
    vertical: bool
    anchored_by: str = 'not anchored'   # 'label on line' | 'leader (n strokes)' | 'below block' | 'not anchored'
    run: object = None        # anchored Run
    leader_strokes: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Equipment
# ─────────────────────────────────────────────────────────────────────────

def find_equipment(page):
    """§6.2.1 — equipment bodies. Primary source: EQ# smart tags, which sit
    exactly on the body outline (the vessel-side label is often absent while
    the title-block list repeats the tag with no drawing). Body = smallest
    mid-weight outline containing the EQ# point. Visible labels without an
    EQ# still register via the merged-nearby-parts heuristic."""
    out = []
    for st in page.smart_tags:
        if not st.text.startswith('EQ#'):
            continue
        tag = st.text[3:]
        # Merge every mid-weight outline containing the EQ# point — the
        # vessel body may be split into several paths, and picking just the
        # smallest/largest single one proved unstable on the reference set.
        hits = []
        for sh in page.shapes:
            x0, y0, x1, y1 = sh.bbox
            if sh.width < S.PIPE_WIDTH_PT - 0.06 or sh.item_count < 2:
                continue
            if not (500 <= sh.area <= 60000):
                continue
            if x0 - 1 <= st.cx <= x1 + 1 and y0 - 1 <= st.cy <= y1 + 1:
                hits.append(sh)
        if not hits:
            continue
        merged = (min(s.bbox[0] for s in hits), min(s.bbox[1] for s in hits),
                  max(s.bbox[2] for s in hits), max(s.bbox[3] for s in hits))
        body = type('Body', (), {'bbox': merged, 'item_count': sum(s.item_count for s in hits), 'area': 0})
        out.append(Equipment(tag=tag, page=st.page, label_bbox=merged,
                             body_bbox=merged, name=''))
        out[-1]._parts = body.item_count

    # label-driven fallback for equipment without EQ#
    for sp in page.spans:
        if sp.size < S.EQUIP_LABEL_MIN_SIZE_PT or sp.size > 20:
            continue
        text = sp.text.replace(' ', '')
        if not _EQUIP_RE.match(text):
            continue
        if any(e.tag == text for e in out):
            continue
        parts = []
        for sh in page.shapes:
            if sh.width < S.PIPE_WIDTH_PT - 0.06 or sh.item_count < 2:
                continue
            if not (0 < sh.area < 40000):
                continue
            scx, scy = (sh.bbox[0] + sh.bbox[2]) / 2, (sh.bbox[1] + sh.bbox[3]) / 2
            d = ((scx - sp.cx) ** 2 + (scy - sp.cy) ** 2) ** 0.5
            if d <= S.EQUIP_LABEL_PROXIMITY_PT + 40:
                parts.append(sh)
        best = None
        if parts:
            merged = (min(s.bbox[0] for s in parts), min(s.bbox[1] for s in parts),
                      max(s.bbox[2] for s in parts), max(s.bbox[3] for s in parts))
            if (merged[2] - merged[0]) * (merged[3] - merged[1]) < 60000:
                best = (type('Body', (), {'bbox': merged, 'item_count': len(parts)}), 0)
        eq = Equipment(
            tag=text, page=sp.page, label_bbox=sp.bbox,
            body_bbox=best[0].bbox if best else sp.bbox,
            name='',
        )
        eq._parts = len(parts) if best else 0
        out.append(eq)
    # drop pure title-block repeats: no body parts AND sitting in the
    # title-block band (bottom-right corner of the sheet)
    filtered = []
    for eq in out:
        if getattr(eq, '_parts', 0) == 0 and eq.body_bbox == eq.label_bbox:
            x, y = (eq.label_bbox[0] + eq.label_bbox[2]) / 2, (eq.label_bbox[1] + eq.label_bbox[3]) / 2
            if y > page.height * 0.75 and x > page.width * 0.4:
                continue
            if y < page.height * 0.08:
                continue
        filtered.append(eq)
    return filtered


# ─────────────────────────────────────────────────────────────────────────
# Pipes and runs
# ─────────────────────────────────────────────────────────────────────────

def pipe_segments(page, pipe_pen=S.PIPE_PEN_DEFAULT):
    """§6.2.2 — 0.48 pt strokes in the pipe-pen colour; segments < 3 pt are
    dropped. Black 0.48 pt strokes (any orientation — leaders are often
    diagonal) are returned separately as leaders."""
    pipes, leaders = [], []
    for seg in page.strokes:
        if abs(seg.width - S.PIPE_WIDTH_PT) > S.PIPE_WIDTH_TOL_PT:
            continue
        if seg.length < S.PIPE_MIN_SEGMENT_PT:
            continue
        is_pipe_colour = (seg.color == _RED) if pipe_pen == 'red' else (seg.color != _RED)
        if is_pipe_colour and (seg.horizontal or seg.vertical):
            pipes.append(seg)
        elif not is_pipe_colour:
            leaders.append(seg)
    return pipes, leaders


def build_runs(segments, page_no):
    """§6.2.3 — merge collinear pipe pieces with gaps ≤ 60 pt into runs;
    link runs that meet at elbows/tees (endpoint within 1.5 pt of the other
    run's span)."""
    # bucket by axis and fixed coordinate (±1 pt tolerance)
    buckets = {}
    for seg in segments:
        axis = 'h' if seg.horizontal else 'v'
        fixed = (seg.p0[1] if axis == 'h' else seg.p0[0])
        a, b = sorted([seg.p0[0] if axis == 'h' else seg.p0[1],
                       seg.p1[0] if axis == 'h' else seg.p1[1]])
        key = None
        for k in buckets:
            if k[0] == axis and abs(k[1] - fixed) <= 1.0:
                key = k
                break
        if key is None:
            key = (axis, fixed)
            buckets[key] = []
        buckets[key].append((a, b))

    runs = []
    for (axis, fixed), spans_ in buckets.items():
        spans_.sort()
        cur_a, cur_b = spans_[0]
        for a, b in spans_[1:]:
            if a - cur_b <= S.PIPE_RUN_MAX_GAP_PT:
                cur_b = max(cur_b, b)
            else:
                runs.append(Run(axis, fixed, cur_a, cur_b, page_no))
                cur_a, cur_b = a, b
        runs.append(Run(axis, fixed, cur_a, cur_b, page_no))

    # link runs meeting at elbows/tees
    for i, r1 in enumerate(runs):
        for j, r2 in enumerate(runs):
            if i >= j:
                continue
            for p in ((r1.start, r1.fixed) if r1.axis == 'v' else (r1.fixed, r1.start),
                      (r1.end, r1.fixed) if r1.axis == 'v' else (r1.fixed, r1.end)):
                pass
            # endpoint proximity between perpendicular runs
            e1 = [(r1.start, r1.fixed), (r1.end, r1.fixed)] if r1.axis == 'v' \
                else [(r1.fixed, r1.start), (r1.fixed, r1.end)]
            for (ex, ey) in e1:
                if r2.axis == 'h':
                    hit = abs(r2.fixed - ey) <= 1.5 and r2.start - 1.5 <= ex <= r2.end + 1.5
                else:
                    hit = abs(r2.fixed - ex) <= 1.5 and r2.start - 1.5 <= ey <= r2.end + 1.5
                if hit:
                    r1.links.append(j)
                    r2.links.append(i)
                    break
    return runs


# ─────────────────────────────────────────────────────────────────────────
# Line labels and anchoring
# ─────────────────────────────────────────────────────────────────────────

def find_line_labels(page):
    """§6.2.4 — label spans (spaces removed), horizontal or vertical; the
    block = label + design-condition rows `1.`–`4.` directly under it."""
    labels = []
    for sp in page.spans:
        if sp.size <= S.SMART_TAG_MAX_SIZE_PT:
            continue
        text = sp.text.replace(' ', '').rstrip('.')
        m = _LABEL_RE.fullmatch(text)
        if not m:
            continue
        size, unit, service, serial, pclass, insul = m.groups()
        line = f'{size}-{unit}-{service}-{serial}-{pclass}' + (f'-{insul}' if insul else '')
        # condition rows 1.–4. under the label
        rows = [r for r in page.spans
                if re.match(r'^[1-4]\.', r.text.strip())
                and abs(r.cx - sp.cx) < 60 and 0 < r.bbox[1] - sp.bbox[3] < 40]
        bbox = (min([sp.bbox[0]] + [r.bbox[0] for r in rows]),
                sp.bbox[1],
                max([sp.bbox[2]] + [r.bbox[2] for r in rows]),
                max([sp.bbox[3]] + [r.bbox[3] for r in rows]))
        labels.append(LineLabel(
            line=line, size=size, unit=unit, service_code=service, serial=serial,
            pipe_class=pclass, insulation=insul or '', page=sp.page, bbox=bbox,
            vertical=sp.vertical,
        ))
    # de-duplicate identical labels on the same page (leader repeats)
    seen, unique = set(), []
    for lb in labels:
        key = (lb.line, round(lb.bbox[0]), round(lb.bbox[1]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(lb)
    return unique


def anchor_labels(labels, runs, leaders, page):
    """§6.2.5 — anchor each label to its pipe run, in order:
    label-on-line → leader (≤6 strokes) → below block → not anchored."""
    for lb in labels:
        x0, y0, x1, y1 = lb.bbox
        # 1. label on line: nearest run whose x-span overlaps the label and
        # that passes just below it. Measured from the block bottom because
        # hold/flag boxes around labels add up to ~35 pt of hatching (the
        # 72009 reference label sits 33 pt above its pipe inside such a box).
        best = None
        for run in runs:
            if run.page != lb.page:
                continue
            if not lb.vertical and run.axis == 'h':
                gap = run.fixed - y1
                overlap = min(x1, run.end) - max(x0, run.start)
                if -1 <= gap <= 40 and overlap > (x1 - x0) * 0.3:
                    if best is None or gap < best[0]:
                        best = (gap, run)
            elif lb.vertical and run.axis == 'v':
                gap = run.fixed - x1
                overlap = min(y1, run.end) - max(y0, run.start)
                if -1 <= gap <= 40 and overlap > (y1 - y0) * 0.3:
                    if best is None or gap < best[0]:
                        best = (gap, run)
        if best:
            lb.run = best[1]
            lb.anchored_by = 'label on line'
            continue

        # 2. leader: follow non-pipe strokes from the block border, ≤6 strokes.
        # Strokes with BOTH ends inside the block are the label's own table /
        # flag-box borders (spec §6.2.4) — never leader candidates.
        x0b, y0b, x1b, y1b = lb.bbox
        leader_pool = [s for s in leaders if not (
            x0b - 0.5 <= s.p0[0] <= x1b + 0.5 and y0b - 0.5 <= s.p0[1] <= y1b + 0.5
            and x0b - 0.5 <= s.p1[0] <= x1b + 0.5 and y0b - 0.5 <= s.p1[1] <= y1b + 0.5)]
        tip = _follow_leader(leader_pool, lb)
        if tip is not None:
            for run in runs:
                if run.page != lb.page or run.length < S.LEADER_MIN_PIPE_RUN_PT:
                    continue
                if _point_on_run(run, tip[0], tip[1], 2.0):
                    lb.run = run
                    lb.anchored_by = f'leader ({len(lb.leader_strokes)} strokes)'
                    break
            if lb.run is not None:
                continue

        # 3. below block fallback: nearest run under the block within 25 pt
        best = None
        for run in runs:
            if run.page != lb.page or run.axis != 'h':
                continue
            gap = run.fixed - y1
            if 0 <= gap <= 25 and run.start - 5 <= (x0 + x1) / 2 <= run.end + 5:
                if best is None or gap < best[0]:
                    best = (gap, run)
        if best:
            lb.run = best[1]
            lb.anchored_by = 'below block'
    return labels


def _point_on_run(run, x, y, tol):
    if run.axis == 'h':
        return abs(run.fixed - y) <= tol and run.start - tol <= x <= run.end + tol
    return abs(run.fixed - x) <= tol and run.start - tol <= y <= run.end + tol


def _follow_leader(leaders, lb):
    """Walk connected non-pipe strokes from the label block border (≤6);
    return the final tip point or None."""
    x0, y0, x1, y1 = lb.bbox
    used = []
    # seed: a stroke touching the block border
    start = None
    for seg in leaders:
        pts = [seg.p0, seg.p1]
        for pt in pts:
            if x0 - 1.5 <= pt[0] <= x1 + 1.5 and y0 - 1.5 <= pt[1] <= y1 + 1.5:
                start = seg
                break
        if start:
            break
    if start is None:
        return None
    chain = [start]
    tip = start.p1 if (abs(start.p0[0] - (x0 + x1) / 2) + abs(start.p0[1] - (y0 + y1) / 2)
                       < abs(start.p1[0] - (x0 + x1) / 2) + abs(start.p1[1] - (y0 + y1) / 2)) else start.p0
    while len(chain) < S.LEADER_MAX_STROKES:
        nxt = None
        for seg in leaders:
            if seg in chain:
                continue
            for pt in (seg.p0, seg.p1):
                if abs(pt[0] - tip[0]) <= 1.5 and abs(pt[1] - tip[1]) <= 1.5:
                    nxt = seg
                    tip = seg.p1 if pt == seg.p0 else seg.p0
                    break
            if nxt:
                break
        if not nxt:
            break
        chain.append(nxt)
    lb.leader_strokes = chain
    return tip
