r"""§6.5 — service text generation.

Loop primary = the member wired to equipment, else the valve/element/
transmitter on a pipe, else any member on a pipe. The service string follows
the soft-coded rules table; every service stores its evidence sentence and a
confidence band: High ≥ 0.85 · Review 0.6–0.84 · AI step < 0.6.

Off-page connectors: `\d{2}-P[PU]-152-\d{5,6}-P2` boxes with a `#<id>`; the
fluid is the text just above the box, the other end the text just below.
Direction from the arrow end; unknown direction falls back to the partner
sheet's same #id, then the fluid rule (FLARE/RETURN out, SUPPLY in).
"""
import re
from dataclasses import dataclass

from . import settings as S

_CONNECTOR_RE = re.compile(S.CONNECTOR_RE)

_VARIABLE_NAMES = {'L': 'LEVEL', 'P': 'PRESSURE', 'T': 'TEMPERATURE',
                   'F': 'FLOW', 'A': 'ANALYSER'}

# §4 service templates (project rule set, soft-coded)
HS_TEMPLATES = {  # HS loop suffix → template; <valve> = loop's XV tag
    'C': '{valve} ESD RESET',
    'T': '{valve} ESD TEST RUN/OK',
}
SUFFIX_TEMPLATES = {  # valve member suffix → service suffix (only when the
    'ZSH': 'OPEN', 'ZSL': 'CLOSE', 'ZT': 'LIFT', 'ZI': 'POSITION', 'Y': 'SOV',
}   # base service contains VALVE)


@dataclass
class ServiceResult:
    tag: str
    service: str
    confidence: float
    evidence: str
    alternative: str = ''


@dataclass
class Connector:
    sheet: str
    conn_id: str                # #1059
    drawing_ref: str            # 13-PU-152-…-P2
    fluid: str                  # text above the box
    destination: str            # text below the box
    direction: str              # 'in' | 'out' | ''
    bbox: tuple
    page: int


def find_connectors(page, title_block_text=''):
    """Off-page connector boxes on one page. Seeds on the `#<id>` marker and
    finds the nearest drawing-ref span; fluid = text just above the box,
    destination = text just below."""
    # collect pieces
    id_spans, ref_spans = [], []
    for sp in page.spans:
        if sp.size <= S.SMART_TAG_MAX_SIZE_PT:
            continue
        t = sp.text.strip()
        if re.match(r'^#\d+$', t):
            id_spans.append(sp)
        elif _CONNECTOR_RE.search(t.replace(' ', '')) and t.replace(' ', '') not in title_block_text:
            ref_spans.append(sp)

    out = []
    used_refs = set()
    for idsp in id_spans:
        # nearest drawing-ref span within 45 pt
        ref = None
        for r in ref_spans:
            d = ((r.cx - idsp.cx) ** 2 + (r.cy - idsp.cy) ** 2) ** 0.5
            if d < 45 and (ref is None or d < ref[1]):
                ref = (r, d)
        if ref is None:
            continue
        rsp = ref[0]
        used_refs.add(id(rsp))
        box = (min(idsp.bbox[0], rsp.bbox[0]), min(idsp.bbox[1], rsp.bbox[1]),
               max(idsp.bbox[2], rsp.bbox[2]), max(idsp.bbox[3], rsp.bbox[3]))
        bcx = (box[0] + box[2]) / 2
        # fluid: nearest span just above the box; destination: just below
        above = [s for s in page.spans
                 if s.size > S.SMART_TAG_MAX_SIZE_PT and s is not idsp and s is not rsp
                 and abs(s.cx - bcx) < 55 and 0 < box[1] - s.bbox[3] < 14
                 and not s.text.strip().startswith('#') and not _CONNECTOR_RE.search(s.text.replace(' ', ''))]
        below = [s for s in page.spans
                 if s.size > S.SMART_TAG_MAX_SIZE_PT and s is not idsp and s is not rsp
                 and abs(s.cx - bcx) < 55 and 0 < s.bbox[1] - box[3] < 14
                 and not s.text.strip().startswith('#') and not _CONNECTOR_RE.search(s.text.replace(' ', ''))]
        above.sort(key=lambda s: box[1] - s.bbox[3])
        below.sort(key=lambda s: s.bbox[1] - box[3])
        fluid = above[0].text.strip() if above else ''
        dest = below[0].text.strip() if below else ''
        out.append(Connector(
            sheet='', conn_id=idsp.text.strip().lstrip('#'),
            drawing_ref=_CONNECTOR_RE.search(rsp.text.replace(' ', '')).group(0),
            fluid=fluid, destination=dest, direction='', bbox=box, page=idsp.page,
        ))
    return out


def _connector_direction(page, conn):
    """Arrow end of the box: left-edge box pointing right = flowing in;
    right-edge box pointing right = flowing out. Geometric heuristic: the
    box's pointy side (single vertex) decides."""
    # simplified: boxes on the left half of the sheet flow in, right half out
    mid = page.width / 2
    cx = (conn.bbox[0] + conn.bbox[2]) / 2
    return 'in' if cx < mid else 'out'


def _equip_service(tag, variable_letter):
    name = (tag.equipment or '').replace('-', '')
    var = _VARIABLE_NAMES.get(variable_letter, variable_letter)
    return f'{var} OF {name}' if name else ''


def generate_service(c, members, connectors_by_page, all_loops=None,
                     pages=None, equipment_by_page=None):
    """One ServiceResult for classified tag `c` given its loop members.
    `all_loops` (loop_key → members) lets HS/X members reference the valve
    of the same-number X loop (13-HS-7201C belongs to 13-XV-7201)."""
    tag = c.tag
    var_letter = tag.func[0] if tag.func else ''

    # HS templates (§4) — the valve lives in the same-number X loop
    if tag.func == 'HS':
        suffix = tag.loop[-1].upper() if tag.loop else ''
        tmpl = HS_TEMPLATES.get(suffix)
        digits = re.match(r'^(\d+)', tag.loop).group(1)
        x_members = (all_loops or {}).get(f'{tag.unit}-X-{digits}', members)
        xv = next((m.tag for m in x_members if m.tag.func == 'XV'), None)
        if tmpl and xv:
            valve = f'{xv.unit}{xv.func}{re.match(r"^(\d+)", xv.loop).group(1)}'
            return ServiceResult(tag.tag, tmpl.format(valve=valve), 0.95,
                                 f'HS template ({suffix}) for {xv.tag}')

    # X-loop members: suffix templates off the loop primary's base service
    primary = _loop_primary(members)
    page = (pages or {}).get(tag.page) if pages else None
    equipment = (equipment_by_page or {}).get(tag.page, []) if equipment_by_page else []
    base = _base_service(primary, connectors_by_page, page=page,
                         equipment=equipment, members=members) if primary else None
    if base and 'VALVE' in base.service.upper() and tag is not primary.tag:
        for sfx, word in SUFFIX_TEMPLATES.items():
            if tag.func.endswith(sfx) or (sfx == 'Y' and tag.func.endswith('Y')):
                return ServiceResult(tag.tag, f'{base.service} {word}', base.confidence,
                                     f'{base.evidence}; suffix template {sfx} → {word}')
    if base:
        if tag is not primary.tag:
            return ServiceResult(tag.tag, base.service, base.confidence,
                                 f'{base.evidence}; loop member of {primary.tag.tag}')
        # Same-number family alternative: an X-loop valve whose L/P/T loop
        # sits on equipment the valve's line reaches → offer it, cap at 0.6
        alt = ''
        if var_letter == 'X' and all_loops:
            digits = re.match(r'^(\d+)', tag.loop).group(1)
            for fam in (f'{tag.unit}-L-{digits}', f'{tag.unit}-P-{digits}', f'{tag.unit}-T-{digits}'):
                for m in (all_loops.get(fam) or []):
                    if getattr(m.tag, 'contact', '') == 'equip' and m.tag.equipment:
                        alt = f'ALTERNATIVE: {_equip_service(m.tag, m.tag.func[0])}'
                        break
                if alt:
                    break
        out = base
        if alt:
            out = ServiceResult(tag.tag, base.service, min(base.confidence, 0.6),
                                base.evidence + '; same-number loop on reached equipment',
                                alternative=alt)
        return out
    return ServiceResult(tag.tag, '', 0.0, 'nothing reached — AI step')


def _loop_primary(members):
    """Loop primary: member wired to equipment, else the valve body (XV/FV)
    on a pipe, else a transmitter/element on a pipe, else any member on a
    pipe."""
    for m in members:
        if getattr(m.tag, 'contact', '') == 'equip':
            return m
    for m in members:
        if getattr(m.tag, 'contact', '') == 'pipe' and m.tag.func in ('XV', 'FV'):
            return m
    for m in members:
        if getattr(m.tag, 'contact', '') == 'pipe' and m.tag.func.endswith('T'):
            return m
    for m in members:
        if getattr(m.tag, 'contact', '') == 'pipe' and (
                m.tag.func.startswith(('X', 'F', 'L', 'P', 'T'))):
            return m
    for m in members:
        if getattr(m.tag, 'contact', '') == 'pipe':
            return m
    return members[0] if members else None


def _base_service(member, connectors_by_page, page=None, equipment=None, members=None):
    """The rules table for the loop primary member."""
    tag = member.tag
    var_letter = tag.func[0] if tag.func else ''

    # Anti-surge: F loop with FV + FZT + FY*, and ANTI SURGE / KICK-BACK text
    # near the valve → '<n>ST/ND STAGE COMP. ANTI-SURGE CONTROL VALVE'
    if members and page is not None:
        funcs = {m.tag.func for m in members}
        if any(f == 'FV' for f in funcs) and any(f.startswith('FZT') for f in funcs) \
                and any(f.startswith('FY') for f in funcs):
            # ANTI SURGE / KICK-BACK text sits near the FV (kick-back line),
            # which can be ~200 pt from the FY relay bubble — scan the union
            # of member neighbourhoods.
            near_text = ''
            for m in members:
                near_text += ' ' + ' '.join(
                    s.text for s in page.spans
                    if s.size > S.SMART_TAG_MAX_SIZE_PT
                    and abs(s.cx - m.tag.x) < 200 and abs(s.cy - m.tag.y) < 200).upper()
            if 'ANTI SURGE' in near_text or 'KICK-BACK' in near_text:
                stage = ''
                m_stage = re.search(r'(\d+(?:ST|ND|RD|TH))\s+STAGE', near_text)
                if m_stage:
                    stage = m_stage.group(1) + ' STAGE'
                if not stage:
                    # equipment data box anywhere on the page, prefer text
                    # with SUCTION (the reference drum reads "…COMPRESSOR
                    # 1ST STAGE SUCTION DRUM" at the sheet edge)
                    for s in page.spans:
                        up = s.text.upper()
                        if 'SUCTION' in up and 'STAGE' in up:
                            m2 = re.search(r'(\d+(?:ST|ND|RD|TH))\s+STAGE', up)
                            if m2:
                                stage = m2.group(1) + ' STAGE'
                                break
                if not stage:
                    for eq in (equipment or []):
                        name_zone = ' '.join(
                            s.text for s in page.spans
                            if s.size > S.SMART_TAG_MAX_SIZE_PT
                            and abs(s.cx - (eq.body_bbox[0] + eq.body_bbox[2]) / 2) < 90
                            and abs(s.cy - (eq.body_bbox[1] + eq.body_bbox[3]) / 2) < 120).upper()
                        if 'SUCTION' in name_zone:
                            m = re.search(r'(\d+(?:ST|ND|RD|TH))\s+STAGE', name_zone)
                            if m:
                                stage = m.group(1) + ' STAGE'
                                break
                service = f'{stage + " " if stage else ""}COMP. ANTI-SURGE CONTROL VALVE'.strip()
                return ServiceResult(tag.tag, service, 0.70,
                                     'anti-surge loop (FV + FZT + FY) with ANTI SURGE text near valve')

    # Primary on equipment. X-loop valves skip the variable-name rule —
    # their service comes from the connector rule below (a valve has no
    # "LEVEL OF …" meaning of its own).
    if getattr(tag, 'contact', '') == 'equip' and getattr(tag, 'equipment', '') \
            and var_letter != 'X':
        service = _equip_service(tag, var_letter)
        return ServiceResult(tag.tag, service, 0.95,
                             f'instrument wired to equipment {tag.equipment}')

    # X-loop valve on equipment: connector rule with the equipment name
    if getattr(tag, 'contact', '') == 'equip' and getattr(tag, 'equipment', ''):
        conns = [cn for cn in connectors_by_page.get(tag.page, []) if cn.fluid]
        conns.sort(key=lambda cn: ((cn.bbox[0] + cn.bbox[2]) / 2 - tag.x) ** 2
                                  + ((cn.bbox[1] + cn.bbox[3]) / 2 - tag.y) ** 2)
        if conns:
            conn = conns[0]
            verb = 'FROM' if conn.direction == 'out' else 'TO'
            equip_name = tag.equipment.replace('-', '')
            return ServiceResult(
                tag.tag, f'{conn.fluid} {verb} {equip_name}', 0.85,
                f'connector #{conn.conn_id} ({conn.drawing_ref}); valve on {tag.equipment}')
        return ServiceResult(tag.tag, tag.equipment.replace('-', ''), 0.50,
                             f'valve on {tag.equipment}, no connector reached')

    # Pipe contact: equipment may be at the run's END (nozzle tolerance),
    # and the nearest connector gives the fluid even without a line label.
    if getattr(tag, 'contact', '') == 'pipe':
        run = getattr(tag, '_contact', None) and tag._contact.run
        equip_at_end = ''
        if run is not None and equipment:
            for eq in equipment:
                ex0, ey0, ex1, ey1 = eq.body_bbox
                t = S.CONTACT_EQUIP_NOZZLE_TOL_PT
                endpoints = ([(run.fixed, run.start), (run.fixed, run.end)] if run.axis == 'h'
                             else [(run.start, run.fixed), (run.end, run.fixed)])
                if any(ex0 - t <= px <= ex1 + t and ey0 - t <= py <= ey1 + t
                       for px, py in endpoints):
                    equip_at_end = eq.tag
                    break
        conns = connectors_by_page.get(tag.page, [])
        usable = [cn for cn in conns if cn.fluid]
        usable.sort(key=lambda cn: ((cn.bbox[0] + cn.bbox[2]) / 2 - tag.x) ** 2
                                   + ((cn.bbox[1] + cn.bbox[3]) / 2 - tag.y) ** 2)
        conn = usable[0] if usable else None
        if conn and equip_at_end:
            direction = conn.direction or 'in'
            verb = 'TO' if direction == 'in' else 'FROM'
            equip_name = equip_at_end.replace('-', '')
            return ServiceResult(
                tag.tag, f'{conn.fluid} {verb} {equip_name}', 0.85,
                f'connector #{conn.conn_id} ({conn.drawing_ref}) flows {direction}; '
                f'line run ends at {equip_at_end} nozzle')
        if conn:
            if conn.destination:
                return ServiceResult(tag.tag, f'{conn.fluid} TO {conn.destination}', 0.80,
                                     f'connector #{conn.conn_id} destination {conn.destination}')
            return ServiceResult(tag.tag, f'{conn.fluid} FROM {conn.drawing_ref}', 0.75,
                                 f'connector #{conn.conn_id} only')
        if equip_at_end:
            return ServiceResult(tag.tag, _equip_service(
                type('T', (), {'equipment': equip_at_end})(), var_letter) or '', 0.50,
                f'equipment {equip_at_end} at run end')
    return None
