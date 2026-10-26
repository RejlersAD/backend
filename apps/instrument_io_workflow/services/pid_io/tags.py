"""§6.1 — instrument tags: bubble text pairing, outline classification,
system letters, alarm letters, loop keys, smart-tag confirmation, and
untagged-symbol classification.

On the reference sheets a tag renders as two stacked spans inside a circle:
the top carries unit+function (`13LT`), the bottom the loop number and
suffix (`7201C`). Assembled tag: `13-LT-7201C`. A hidden white 1.1 pt
`IN#<tag>` span sits on the bubble and confirms the pair.
"""
import re
from dataclasses import dataclass, field

from . import settings as S

_TOP_RE = re.compile(S.TAG_TOP_RE)
_BOTTOM_RE = re.compile(S.TAG_BOTTOM_RE)
_ALARM_RE = re.compile(r'^(?:SHH|SLL|HH|LL|SH|SL|H|L)$')


@dataclass
class BubbleTag:
    tag: str                    # 13-LT-7201C
    unit: str                   # 13
    func: str                   # LT
    loop: str                   # 7201C
    loop_key: str               # 13-L-7201
    page: int
    x: float
    y: float
    bbox: tuple
    text_bbox: tuple = None     # the two tag text spans (loop bottom = anchor for system letter)
    system: str = ''            # I/S/P/PX/M/K/H
    bubble: str = 'circle'      # circle | square | bar | dashed
    alarms: list = field(default_factory=list)
    confirmed_by_smart_tag: bool = False
    shape: object = None        # PathShape evidence


def _split_top(text):
    m = re.match(r'^(\d{2})([A-Z]{1,6})$', text)
    return (m.group(1), m.group(2)) if m else (None, None)


def _loop_key(unit, func, loop):
    digits = re.match(r'^(\d{3,5})', loop)
    return f'{unit}-{func[0]}-{digits.group(1)}' if digits else f'{unit}-{func[0]}-{loop}'


def find_bubble_shapes(page):
    """§6.1.3 — a bubble is a closed path of ≥16 segments, 14–28 pt across,
    with a roughly square bbox."""
    out = []
    for sh in page.shapes:
        w = sh.bbox[2] - sh.bbox[0]
        h = sh.bbox[3] - sh.bbox[1]
        if not (S.BUBBLE_MIN_SIZE_PT <= w <= S.BUBBLE_MAX_SIZE_PT
                and S.BUBBLE_MIN_SIZE_PT <= h <= S.BUBBLE_MAX_SIZE_PT):
            continue
        if not sh.square_bbox:
            continue
        if sh.item_count < S.BUBBLE_MIN_SEGMENTS and not sh.closed:
            continue
        out.append(sh)
    return out


def classify_frame(page, shape):
    """From the SAME path read the frame: ≥3 bbox edges present → square
    (DCS function); a centre horizontal bar → bar; neither → circle."""
    x0, y0, x1, y1 = shape.bbox
    tol = S.BUBBLE_SQUARE_EDGE_TOL_PT
    segs = [s for s in page.strokes if s.shape_idx == shape.idx]
    # A polygonal circle is ~60 short segments; a frame edge must SPAN most of
    # the bbox edge (≥60%), otherwise every circle fakes a square frame.
    edges = 0
    bar = False
    min_w = (x1 - x0) * 0.6
    min_h = (y1 - y0) * 0.6
    for seg in segs:
        if seg.horizontal:
            mid_y = (seg.p0[1] + seg.p1[1]) / 2
            if (abs(mid_y - y0) <= tol or abs(mid_y - y1) <= tol) and seg.length >= min_w:
                edges += 1
            if abs(mid_y - (y0 + y1) / 2) <= S.BUBBLE_BAR_TOL_PT and seg.length >= min_w:
                bar = True
        elif seg.vertical:
            mid_x = (seg.p0[0] + seg.p1[0]) / 2
            if (abs(mid_x - x0) <= tol or abs(mid_x - x1) <= tol) and seg.length >= min_h:
                edges += 1
    if edges >= 3:
        return 'square'
    if bar:
        return 'bar'
    return 'circle'


def pair_tags(page):
    """§6.1.2 — pair unit+function spans with loop spans directly below."""
    tops, bottoms = [], []
    for sp in page.spans:
        if sp.size <= S.SMART_TAG_MAX_SIZE_PT:
            continue
        text = sp.text.replace(' ', '')
        if _TOP_RE.match(text):
            tops.append(sp)
        elif _BOTTOM_RE.match(text):
            bottoms.append(sp)

    pairs = []
    used_bottoms = set()
    for top in tops:
        best = None
        for bot in bottoms:
            if id(bot) in used_bottoms or bot.page != top.page:
                continue
            if top.vertical or bot.vertical:
                continue
            dy = bot.bbox[1] - top.bbox[3]
            if not (S.TAG_PAIR_MIN_GAP_PT - 5.0 <= dy <= S.TAG_PAIR_MAX_GAP_PT + 5.0):
                continue
            dx = abs(top.cx - bot.cx)
            if dx > S.TAG_PAIR_MAX_CENTRE_DX_PT + 3.0:
                continue
            score = abs(dy - 7.0) + dx
            if best is None or score < best[0]:
                best = (score, bot)
        if not best:
            continue
        bot = best[1]
        used_bottoms.add(id(bot))
        unit, func = _split_top(top.text.replace(' ', ''))
        loop = bot.text.replace(' ', '')
        tag = f'{unit}-{func}-{loop}'
        bbox = (min(top.bbox[0], bot.bbox[0]), top.bbox[1],
                max(top.bbox[2], bot.bbox[2]), bot.bbox[3])
        pairs.append(BubbleTag(
            tag=tag, unit=unit, func=func, loop=loop,
            loop_key=_loop_key(unit, func, loop), page=top.page,
            x=(bbox[0] + bbox[2]) / 2, y=(bbox[1] + bbox[3]) / 2, bbox=bbox,
            text_bbox=bbox,
        ))
    return pairs


def attach_bubbles(page, tags):
    """Assign each tag its bubble outline and classify the frame."""
    shapes = find_bubble_shapes(page)
    for tag in tags:
        cx, cy = tag.x, tag.y
        for sh in shapes:
            x0, y0, x1, y1 = sh.bbox
            if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
                tag.shape = sh
                tag.bbox = sh.bbox
                tag.bubble = classify_frame(page, sh)
                break
    return tags


def attach_system_letters(page, tags):
    """§6.1.4 — system letter centred under the loop TEXT (not the bubble
    outline: on the reference sheets the letter sits inside the bubble,
    directly below the loop number)."""
    for tag in tags:
        x0, y0, x1, y1 = tag.text_bbox or tag.bbox
        for sp in page.spans:
            if sp.size <= S.SMART_TAG_MAX_SIZE_PT:
                continue
            t = sp.text.strip()
            if t not in S.SYSTEM_LETTERS:
                continue
            dy = sp.bbox[1] - y1
            if not (S.SYSTEM_LETTER_MIN_DY_PT <= dy <= S.SYSTEM_LETTER_MAX_DY_PT):
                continue
            if abs(sp.cx - (x0 + x1) / 2) > S.SYSTEM_LETTER_MAX_DX_PT:
                continue
            tag.system = t
            break
    return tags


def attach_alarms(page, tags):
    """§6.1.5 — alarm letters in the zone around the bubble, not red."""
    l, u, r, d = S.ALARM_ZONE
    for tag in tags:
        x0, y0, x1, y1 = tag.bbox
        zone = (x0 + l, y0 + u, x1 + r, y1 + d)
        found = []
        for sp in page.spans:
            if sp.size <= S.SMART_TAG_MAX_SIZE_PT:
                continue
            t = sp.text.strip()
            if not _ALARM_RE.match(t):
                continue
            if S.ALARM_EXCLUDE_RED and sp.color == S.RED_COLOUR:
                continue
            if zone[0] <= sp.cx <= zone[2] and zone[1] <= sp.cy <= zone[3]:
                found.append(t)
        tag.alarms = found
    return tags


def confirm_with_smart_tags(page, tags):
    """Pairs whose tag appears in the hidden IN# layer are confirmed.
    Returns (confirmed_count, pairs_without_in, in_without_pair)."""
    by_tag = {}
    for st in page.smart_tags:
        if st.text.startswith('IN#'):
            by_tag.setdefault(st.text[3:], []).append(st)
    in_without_pair = []
    for tag in tags:
        sts = by_tag.get(tag.tag, [])
        if sts:
            tag.confirmed_by_smart_tag = True
        else:
            in_without_pair.append(tag.tag)
    paired = {t.tag for t in tags}
    orphans = [name for name in by_tag if name not in paired]
    return sum(1 for t in tags if t.confirmed_by_smart_tag), in_without_pair, orphans


def classify_untagged(page, tags):
    """§6.1.7 — bubbles with no tag inside. `I/O` open/close status, `I`
    interlock marker, `O` output marker, `M` motor. Never an IO row."""
    out = []
    tagged_shapes = {id(t.shape) for t in tags if t.shape is not None}
    for sh in find_bubble_shapes(page):
        if id(sh) in tagged_shapes:
            continue
        cx = (sh.bbox[0] + sh.bbox[2]) / 2
        cy = (sh.bbox[1] + sh.bbox[3]) / 2
        inside = [sp.text.strip() for sp in page.spans
                  if sp.size > S.SMART_TAG_MAX_SIZE_PT
                  and sh.bbox[0] <= sp.cx <= sh.bbox[2]
                  and sh.bbox[1] <= sp.cy <= sh.bbox[3]]
        tokens = [t for t in inside if t]
        # Only pure logic/status markers (I, O, I/O, M, P in any
        # combination) are untagged symbols; anything else (nozzle numbers
        # like N06, stray text) is not recorded at all. 'P' appears on the
        # reference sheets as a panel/package status marker next to O.
        if not tokens or any(t not in ('I', 'O', 'I/O', 'M', 'P') for t in tokens):
            continue
        text = ' '.join(tokens)
        kinds = {'I/O': 'open/close status', 'I': 'interlock marker',
                 'O': 'output marker', 'M': 'motor', 'P': 'package marker'}
        if len(set(tokens)) == 1:
            kind = kinds[tokens[0]]
        else:
            kind = ' / '.join(dict.fromkeys(kinds[t] for t in tokens))
        nearest, dist = None, None
        for t in tags:
            d = ((t.x - cx) ** 2 + (t.y - cy) ** 2) ** 0.5
            if dist is None or d < dist:
                nearest, dist = t.tag, d
        out.append({
            'page': sh.page, 'text': text, 'kind': kind,
            'nearest_tag': nearest, 'distance_pt': round(dist or 0, 1),
            'bbox': sh.bbox,
        })
    return out
