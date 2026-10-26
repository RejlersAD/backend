"""§6.6 — set points.

Vessel level scales are drawn as RED label/value pairs beside the vessel:
labels like HL=SHH / HA / NL / LA / LL (also HH, SHH, SLL, LH, MH) with red
values (+/-2..5 digits) 0–20 pt to the right on the same text line, within
80 pt of the vessel body. A bubble's alarm letter maps to its scale row:

  SHH/HH → HL=SHH, HH, HL · H/SH → HA · L/SL → LA · LL/SLL → LL=SLL, LL

PSV: `<n> barg` text ≤26 pt under the bubble → set pressure.

Target IO-list column by alarm letter:
  H/SH → High_Alarm_Value · HH/SHH → High_High_Alarm_Value
  L/SL → Low_Alarm_Value  · LL/SLL → Low_Low_Alarm_Value

Percent conversion needs the instrument range (mm) from the IO list baseline
(DCS_Range_Min/Max); without it the mm value is kept and flagged
'Range needed'.
"""
import re
from dataclasses import dataclass

from . import settings as S

_SCALE_LABELS = ('HL=SHH', 'LL=SLL', 'HL', 'HA', 'NL', 'LA', 'LL',
                 'HH', 'SHH', 'SLL', 'LH', 'MH')
_VALUE_RE = re.compile(rf'^({"|".join(re.escape(l) for l in _SCALE_LABELS)})$')
_NUM_RE = re.compile(rf'^{S.SETPOINT_VALUE_RE}$')
_PSV_RE = re.compile(rf'^({S.PSV_SET_RE})$', re.IGNORECASE)

_ALARM_TO_LABELS = {
    'SHH': ('HL=SHH', 'HH', 'HL'), 'HH': ('HL=SHH', 'HH', 'HL'),
    'H': ('HA',), 'SH': ('HA',),
    'L': ('LA',), 'SL': ('LA',),
    'LL': ('LL=SLL', 'LL'), 'SLL': ('LL=SLL', 'LL'),
}
_ALARM_TO_COLUMN = {
    'H': 'High_Alarm_Value', 'SH': 'High_Alarm_Value',
    'HH': 'High_High_Alarm_Value', 'SHH': 'High_High_Alarm_Value',
    'L': 'Low_Alarm_Value', 'SL': 'Low_Alarm_Value',
    'LL': 'Low_Low_Alarm_Value', 'SLL': 'Low_Low_Alarm_Value',
}


@dataclass
class SetPoint:
    tag: str
    point: str                  # alarm letter, e.g. SHH
    value: str                  # +700 (mm kept as read)
    unit: str                   # 'mm' | 'barg'
    label: str                  # scale label, e.g. HL=SHH
    column: str                 # target IO-list column
    page: int
    evidence: str

    def display(self):
        suffix = f' ({self.label})' if self.label else ''
        return f'{self.point} = {self.value} {self.unit}{suffix}'


def _red_spans(page):
    return [s for s in page.spans if s.color == S.RED_COLOUR
            and s.size > S.SMART_TAG_MAX_SIZE_PT]


def read_vessel_scales(page, equipment):
    """All red label→value pairs within 80 pt of an equipment body."""
    reds = _red_spans(page)
    scales = []
    for lab in reds:
        if not _VALUE_RE.match(lab.text.strip()):
            continue
        # value: red number 0–20 pt to the right, same text line
        val = None
        for cand in reds:
            if cand is lab or not _NUM_RE.match(cand.text.strip()):
                continue
            if abs(cand.bbox[1] - lab.bbox[1]) > 2.0:
                continue
            dx = cand.bbox[0] - lab.bbox[2]
            if 0 <= dx <= S.SETPOINT_VALUE_MAX_DX_PT:
                val = cand
                break
        if val is None:
            continue
        # within 80 pt of a vessel body
        near_eq = None
        for eq in equipment:
            ex0, ey0, ex1, ey1 = eq.body_bbox
            d = max(ex0 - lab.cx, lab.cx - ex1, ey0 - lab.cy, lab.cy - ey1, 0)
            if d <= S.SETPOINT_SCALE_MAX_DIST_PT:
                if near_eq is None or d < near_eq[1]:
                    near_eq = (eq, d)
        scales.append({
            'label': lab.text.strip(), 'value': val.text.strip(),
            'y': lab.cy, 'equipment': near_eq[0].tag if near_eq else '',
            'bbox': lab.bbox,
        })
    return scales


def extract_setpoints(classified, scales_by_page, psv_by_page):
    """Attach set points to tags: alarm letters → vessel scale values; PSV
    barg text under the bubble."""
    out = []
    for c in classified:
        tag = c.tag
        page_scales = scales_by_page.get(tag.page, [])
        for letter in tag.alarms:
            labels = _ALARM_TO_LABELS.get(letter)
            if not labels:
                continue
            match = None
            for sc in page_scales:
                if sc['label'] in labels:
                    # same loop's vessel: prefer the closest scale row
                    d = abs(sc['y'] - tag.y)
                    if match is None or d < match[1]:
                        match = (sc, d)
            if match:
                sc = match[0]
                out.append(SetPoint(
                    tag=tag.tag, point=letter, value=sc['value'], unit='mm',
                    label=sc['label'], column=_ALARM_TO_COLUMN[letter], page=tag.page,
                    evidence=f"red scale {sc['label']} = {sc['value']} mm near {sc['equipment'] or 'vessel'}",
                ))
            else:
                out.append(SetPoint(
                    tag=tag.tag, point=letter, value='', unit='mm',
                    label='', column=_ALARM_TO_COLUMN[letter], page=tag.page,
                    evidence='Letter only — value comes from C&E / datasheet',
                ))
        # PSV set pressure
        for psv in psv_by_page.get(tag.page, []):
            x0, y0, x1, y1 = tag.bbox
            if abs(psv.cx - (x0 + x1) / 2) < 12 and 0 < psv.bbox[1] - y1 <= S.PSV_SET_MAX_DY_PT:
                out.append(SetPoint(
                    tag=tag.tag, point='SET', value=psv.text.replace(' ', ''), unit='barg',
                    label='set pressure', column='Set point', page=tag.page,
                    evidence=f'PSV text under bubble: {psv.text}',
                ))
    return out


def find_psv_pressures(page):
    """`<n> barg` texts — PSV set pressures."""
    return [s for s in page.spans if _PSV_RE.match(s.text.strip())
            and s.size > S.SMART_TAG_MAX_SIZE_PT]


def to_percent(value_mm: str, lrv: float, urv: float):
    """% = (SP − LRV) / (URV − LRV) × 100. None when the range is unknown."""
    try:
        sp = float(value_mm)
    except (TypeError, ValueError):
        return None
    if urv == lrv:
        return None
    return round((sp - lrv) / (urv - lrv) * 100, 2)
