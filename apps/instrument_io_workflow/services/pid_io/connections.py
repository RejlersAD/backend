"""§6.3–6.4 — instrument → process connection, and line number per tag.

Connection, in order:
  1. connection line: walk solid 0.24 pt strokes from the bubble edge until
     a pipe run (≤1.6 pt), an equipment body (+18 pt nozzle tolerance), or
     another bubble;
  2. leader: a black 0.48 pt diagonal (4–40 pt) from the bubble edge, tip
     extended ≤22 pt to the nearest pipe run ≥12 pt or an equipment body;
  3. otherwise device/none — the loop decides.

Line number: the label anchored on the contact's run (nearest along the run,
second-nearest stored as alternative); else walk linked runs ≤3 hops; loop
members without a contact inherit the loop process member's line.
"""
from dataclasses import dataclass, field

from . import settings as S
from .pipes import _point_on_run


@dataclass
class Contact:
    kind: str                 # 'pipe' | 'equip' | 'device' | 'none'
    run: object = None
    equipment: object = None
    point: tuple = None         # where the walk ended
    via: str = ''               # 'connection line' | 'leader' | ''


def _walk_signal_line(page, tag, runs, equipment):
    """Walk solid 0.24 pt strokes from the bubble edge."""
    x0, y0, x1, y1 = tag.bbox
    signals = [s for s in page.strokes
               if abs(s.width - S.SIGNAL_WIDTH_PT) <= 0.06 and not s.dashed
               and s.length > S.SIGNAL_MIN_LEN_PT]
    # seed: stroke starting at the bubble edge
    chain = []
    current_pt = None
    for seg in signals:
        for pt in (seg.p0, seg.p1):
            if x0 - 1.0 <= pt[0] <= x1 + 1.0 and y0 - 1.0 <= pt[1] <= y1 + 1.0:
                chain.append(seg)
                current_pt = seg.p1 if pt == seg.p0 else seg.p0
                break
        if chain:
            break
    if not chain:
        return None
    # walk up to 12 connected strokes
    for _ in range(12):
        # contact?
        hit = _contact_at(current_pt, runs, equipment)
        if hit:
            return chain, hit
        nxt = None
        for seg in signals:
            if seg in chain:
                continue
            for pt in (seg.p0, seg.p1):
                if abs(pt[0] - current_pt[0]) <= 1.2 and abs(pt[1] - current_pt[1]) <= 1.2:
                    nxt = seg
                    current_pt = seg.p1 if pt == seg.p0 else seg.p0
                    break
            if nxt:
                break
        if not nxt:
            break
        chain.append(nxt)
    return chain, _contact_at(current_pt, runs, equipment)


def _contact_at(pt, runs, equipment):
    if pt is None:
        return None
    x, y = pt
    for run in runs:
        if _point_on_run(run, x, y, S.CONTACT_PIPE_TOL_PT):
            return Contact('pipe', run=run, point=pt)
    for eq in equipment:
        ex0, ey0, ex1, ey1 = eq.body_bbox
        t = S.CONTACT_EQUIP_NOZZLE_TOL_PT
        if ex0 - t <= x <= ex1 + t and ey0 - t <= y <= ey1 + t:
            return Contact('equip', equipment=eq, point=pt)
    return None


def _leader_contact(page, tag, runs, equipment):
    """A black 0.48 pt diagonal (4–40 pt) from the bubble edge; tip extended
    ≤22 pt to the nearest pipe run ≥12 pt or an equipment body."""
    x0, y0, x1, y1 = tag.bbox
    for seg in page.strokes:
        if abs(seg.width - S.LEADER_WIDTH_PT) > S.PIPE_WIDTH_TOL_PT:
            continue
        if seg.horizontal or seg.vertical:
            continue
        if not (S.LEADER_MIN_LEN_PT <= seg.length <= S.LEADER_MAX_LEN_PT):
            continue
        for base, tip in ((seg.p0, seg.p1), (seg.p1, seg.p0)):
            if not (x0 - 1.0 <= base[0] <= x1 + 1.0 and y0 - 1.0 <= base[1] <= y1 + 1.0):
                continue
            # extend the tip along the segment direction
            dx, dy = tip[0] - base[0], tip[1] - base[1]
            norm = (dx ** 2 + dy ** 2) ** 0.5 or 1.0
            ext = (tip[0] + dx / norm * S.LEADER_TIP_EXTEND_PT,
                   tip[1] + dy / norm * S.LEADER_TIP_EXTEND_PT)
            best = None
            for run in runs:
                if run.length < S.LEADER_MIN_RUN_PT:
                    continue
                for pt in (tip, ext):
                    if _point_on_run(run, pt[0], pt[1], 2.0):
                        if best is None or run.length < best.length:
                            best = run
            if best:
                return Contact('pipe', run=best, point=tip)
            for eq in equipment:
                ex0, ey0, ex1, ey1 = eq.body_bbox
                t = S.CONTACT_EQUIP_NOZZLE_TOL_PT
                if ex0 - t <= ext[0] <= ex1 + t and ey0 - t <= ext[1] <= ey1 + t:
                    return Contact('equip', equipment=eq, point=tip)
    return None


def find_contact(page, tag, runs, equipment):
    walked = _walk_signal_line(page, tag, runs, equipment)
    if walked and walked[1]:
        chain, contact = walked
        contact.via = 'connection line'
        return contact
    led = _leader_contact(page, tag, runs, equipment)
    if led:
        led.via = 'leader'
        return led
    return Contact('none', via='')


def assign_lines(classified, labels, runs):
    """§6.4 — line number per tag, per-line tag lists, loop inheritance."""
    by_run = {}
    for lb in labels:
        if lb.run is not None:
            by_run.setdefault(id(lb.run), []).append(lb)

    for c in classified:
        tag = c.tag
        tag.contact = 'none'
        tag.line = ''
        tag.line_source = ''
        tag.line_alt = ''
        tag.equipment = ''
        contact = getattr(tag, '_contact', None)
        if contact is None:
            continue
        tag.contact = contact.kind
        if contact.kind == 'equip' and contact.equipment is not None:
            tag.equipment = contact.equipment.tag
        if contact.kind != 'pipe' or contact.run is None:
            continue
        run = contact.run
        cands = by_run.get(id(run), [])
        if cands:
            # nearest along the run; second-nearest as alternative
            pos = contact.point[0] if run.axis == 'h' else contact.point[1]
            def _dist(lb):
                lx = (lb.bbox[0] + lb.bbox[2]) / 2
                ly = (lb.bbox[1] + lb.bbox[3]) / 2
                lp = lx if run.axis == 'h' else ly
                return abs(lp - pos)
            cands.sort(key=_dist)
            tag.line = cands[0].line
            tag.line_source = f'own tap ({contact.via or "run"})'
            if len(cands) > 1:
                tag.line_alt = cands[1].line
            continue
        # walk linked runs ≤3 hops
        seen, frontier, hops = {id(run)}, [run], 0
        found = None
        while frontier and hops < S.LINE_VIA_RUN_MAX_HOPS and not found:
            nxt = []
            for r in frontier:
                for li in r.links:
                    lr = runs[li]
                    if id(lr) in seen:
                        continue
                    seen.add(id(lr))
                    if by_run.get(id(lr)):
                        found = by_run[id(lr)][0]
                        break
                    nxt.append(lr)
                if found:
                    break
            frontier, hops = nxt, hops + 1
        if found:
            tag.line = found.line
            tag.line_source = f'via {hops + 1} connected run(s)'

    # Loop inheritance: members without their own contact take the line of
    # the loop's process member (first member with a line).
    loops = {}
    for c in classified:
        loops.setdefault(c.tag.loop_key, []).append(c)
    for members in loops.values():
        donor = next((m for m in members if m.tag.line), None)
        if not donor:
            continue
        for m in members:
            if not m.tag.line:
                m.tag.line = donor.tag.line
                m.tag.line_source = f'inherited from {donor.tag.tag}'
    return classified
