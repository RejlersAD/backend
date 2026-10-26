"""§6.7 — IO class: which bubbles become IO rows, and which kind.

Decision order matters (first match wins):
  1. dashed bubble              → shown on another P&ID
  2. system P/PX                → Package PLC (vendor) — no DCS/ESD row
  3. square / bar frame         → Soft (DCS/ESD function)
  4. NO_IO_FUNCTIONS            → No IO (local / mechanical / valve body)
  5. circle + I/S by function   → DCS/ESD AI/DI/DO/AO
  6. otherwise                  → no IO row

Plus the valve-typical derivation: an ESD valve XV whose loop carries an
XZS…T test switch gets a derived XZC-<loop> (AO, positioner feedback),
flagged Derived.
"""
from dataclasses import dataclass

from . import settings as S

# Alarm-letter suffixes that make a ZS/HS family a DI (soft-coded: legend data).
# The family may carry a prefix letter for the loop's leading function
# (XZSH, PZSL, FZSH …), so membership is by containment, not startswith.
def _is_switch_family(func: str) -> bool:
    return func.startswith('HS') or 'ZS' in func
_AI_ENDINGS = ('T',)          # transmitters (LT, PT, TT, FT …) and ZT
_DO_ENDINGS = ('Y',)          # solenoid / relay outputs (XY, FY, PY …)
_AO_ENDINGS = ('I', 'C')      # field indicators / positioners


@dataclass
class ClassifiedTag:
    tag: object               # BubbleTag
    io_class: str             # 'AI'|'AO'|'DI'|'DO'|'soft'|'package'|'none'|'other-pid'
    io_type: str              # display label, e.g. 'DCS DI'
    reason: str               # evidence sentence for the UI

    @property
    def is_field_io(self):
        return self.io_class in ('AI', 'AO', 'DI', 'DO')


def classify_tag(tag) -> ClassifiedTag:
    system = tag.system
    sysname = {'I': 'DCS', 'S': 'ESD'}.get(system, '')
    func = tag.func

    if tag.bubble == 'dashed':
        return ClassifiedTag(tag, 'other-pid', 'Shown on other P&ID',
                             'dashed bubble — keep the IO list P&ID column')
    if system in S.PACKAGE_SYSTEMS:
        return ClassifiedTag(tag, 'package', 'Package PLC (vendor)',
                             f'system letter {system} — vendor package, no DCS/ESD row')
    if tag.bubble in ('square', 'bar'):
        return ClassifiedTag(tag, 'soft', 'Soft (DCS/ESD function)',
                             f'{tag.bubble} frame — software function, not a field device')
    if func in S.NO_IO_FUNCTIONS:
        return ClassifiedTag(tag, 'none', 'No IO (local / mechanical / valve body)',
                             f'function {func} is a local/mechanical device or valve body')
    if system in ('I', 'S'):
        if _is_switch_family(func):
            return ClassifiedTag(tag, 'DI', f'{sysname} DI',
                                 f'circle + {system}, function {func} — switch/pushbutton')
        if func.endswith(_AI_ENDINGS):
            return ClassifiedTag(tag, 'AI', f'{sysname} AI',
                                 f'circle + {system}, function {func} — transmitter')
        if func.endswith(_DO_ENDINGS):
            return ClassifiedTag(tag, 'DO', f'{sysname} DO',
                                 f'circle + {system}, function {func} — output')
        if func.endswith(_AO_ENDINGS):
            return ClassifiedTag(tag, 'AO', f'{sysname} AO (field indicator/positioner)',
                                 f'circle + {system}, function {func} — indicator/positioner')
    return ClassifiedTag(tag, 'none', 'No IO (unclassified)',
                         'no system letter / unmatched function — not an IO row')


def classify_all(tags):
    return [classify_tag(t) for t in tags]


def derive_valve_typical_rows(classified):
    """§4 valve typical: an ESD valve XV with an XZS…T test switch in the same
    loop gets a derived XZC-<loop> row (AO, positioner feedback), flagged
    Derived. Returns synthetic ClassifiedTag-like dicts."""
    loops = {}
    for c in classified:
        loops.setdefault(c.tag.loop_key, []).append(c)

    derived = []
    for loop_key, members in loops.items():
        funcs = {m.tag.func for m in members}
        has_xv = any(f == 'XV' for f in funcs)
        test_switches = [m for m in members
                         if m.tag.func.startswith('XZS') and m.tag.loop.upper().endswith('T')]
        if not (has_xv and test_switches):
            continue
        esd = any(m.tag.system == 'S' for m in members)
        if not esd:
            continue
        unit = members[0].tag.unit
        loop_digits = ''.join(ch for ch in members[0].tag.loop if ch.isdigit())
        xv = next(m for m in members if m.tag.func == 'XV')
        derived.append({
            'tag': f'{unit}-XZC-{loop_digits}',
            'unit': unit, 'func': 'XZC', 'loop': loop_digits,
            'loop_key': loop_key, 'page': xv.tag.page,
            'x': xv.tag.x, 'y': xv.tag.y,
            'system': 'S', 'bubble': 'derived',
            'io_class': 'AO', 'io_type': 'ESD AO (positioner feedback)',
            'derived': True,
            'reason': (f'Derived from valve typical: ESD valve {xv.tag.tag} with test '
                       f'switch {test_switches[0].tag.tag} — positioner feedback added'),
        })
    return derived
