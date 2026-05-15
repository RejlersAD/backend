"""
SmartPlant 3D Spec/Catalog Export — Soft-Coded Configuration.
=============================================================

This module defines (purely as data + thin builder closures):

  • Template file paths
  • Per-sheet write rules (Head/Start/End markers, column-name maps)
  • Component-type → CAT sheet routing rules
  • S3D commodity-type tokens, symbol-definition strings, ICC prefixes
  • Standard NPD ladder (ASME B36.10 / B36.19)
  • Value normalisers (NPD, temperature, pressure)

⚠  Only this file should be edited to retune the output schema —
    `smartplant_exporter.py` contains zero hardcoded sheet/column names.

Format reference: Hexagon / Intergraph SmartPlant 3D Reference Data
Generator XLSX (the two `LS1E-A3_*.xlsx` reference workbooks shipped
in `services/templates/`).
"""
from __future__ import annotations

import os
import re

# ─────────────────────────────────────────────────────────────────────────────
# 1. TEMPLATE FILES
# ─────────────────────────────────────────────────────────────────────────────
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')

SPEC_TEMPLATE_PATH = os.path.abspath(
    os.path.join(_TEMPLATE_DIR, 'smartplant_spec_template.xlsx')
)
CAT_TEMPLATE_PATH = os.path.abspath(
    os.path.join(_TEMPLATE_DIR, 'smartplant_cat_template.xlsx')
)

# Marker tokens that appear in column A of every SP3D bulkload sheet.
HEADER_MARKERS = ('Head',)
START_MARKERS  = ('Start',)
END_MARKERS    = ('End',)

# Output filename templates (formatted with job_id).
SPEC_OUTPUT_FILENAME_TPL = 'spec_customisation_{job_id}_SPEC.xlsx'
CAT_OUTPUT_FILENAME_TPL  = 'spec_customisation_{job_id}_CAT.xlsx'


# ─────────────────────────────────────────────────────────────────────────────
# 2. STANDARD NPD LADDER (inches, ASME B36.10)
# ─────────────────────────────────────────────────────────────────────────────
STD_NPDS_INCHES = [
    0.125, 0.25, 0.375, 0.5, 0.75,
    1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12,
    14, 16, 18, 20, 24, 26, 28, 30, 32, 34, 36, 42, 48,
]

# Default S3D piping enumeration codes (kept here so they remain a single,
# audited source of truth — see SP3D Reference Data Guide).
S3D_DEFAULTS = {
    'NpdUnitType':            'in',
    'PipingPointBasis':       '15',     # NPD-only routing
    'EndStandard':            '5',      # ASME / ANSI (project default)
    'FlowDirection_Bidir':    '3',
    'FlowDirection_OutOnly':  '4',
    'EndPrep_BW':             '301',    # Butt Weld
    'EndPrep_RF':             '21',     # Raised Face flange
    'EndPrep_SW':             '441',    # Socket Weld
    'EndPrep_THR':            '331',    # Threaded
    'GraphicalRep':           '15',
}

# Normalisation map for the AI-extracted `end_connection` token.
_END_PREP_LOOKUP = {
    'bw':  S3D_DEFAULTS['EndPrep_BW'],   'butt weld': S3D_DEFAULTS['EndPrep_BW'],
    'rf':  S3D_DEFAULTS['EndPrep_RF'],   'raised face': S3D_DEFAULTS['EndPrep_RF'],
    'sw':  S3D_DEFAULTS['EndPrep_SW'],   'socket weld': S3D_DEFAULTS['EndPrep_SW'],
    'thr': S3D_DEFAULTS['EndPrep_THR'],  'threaded':    S3D_DEFAULTS['EndPrep_THR'],
    'npt': S3D_DEFAULTS['EndPrep_THR'],
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. VALUE NORMALISERS
# ─────────────────────────────────────────────────────────────────────────────
_NPD_FRACTION_MAP = {
    '1/8': 0.125, '1/4': 0.25, '3/8': 0.375,
    '1/2': 0.5,   '3/4': 0.75,
    '1-1/4': 1.25, '1 1/4': 1.25,
    '1-1/2': 1.5,  '1 1/2': 1.5,
    '2-1/2': 2.5,  '2 1/2': 2.5,
}


# Characters that may follow an inch number across various source notations.
_INCH_TOKENS = ('"', '“', '”', '″', '′', "''", "'", 'IN', 'in', 'In', 'inch', 'INCH', 'Inch')


def _to_float_npd(size) -> float | None:
    """Parse an NPD size string ('1/2"', '2”', '2-1/2"', '1 1/2″') → float inches.
    Returns None when un-parseable."""
    if size in (None, ''):
        return None
    s = str(size)
    for tok in _INCH_TOKENS:
        s = s.replace(tok, '')
    s = s.replace('-', ' ').strip()
    if not s:
        return None
    if s in _NPD_FRACTION_MAP:
        return _NPD_FRACTION_MAP[s]
    # Mixed fraction: '1 1/2', '2 1/4'
    if ' ' in s and '/' in s:
        try:
            whole, frac = s.split(' ', 1)
            num, den = frac.split('/', 1)
            return float(whole) + float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass
    # Pure fraction: '1/2', '3/4'
    if '/' in s:
        try:
            num, den = s.split('/', 1)
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_npd(size) -> str:
    """Stringify an NPD value in S3D format (integer when whole, else decimal)."""
    v = _to_float_npd(size)
    if v is None:
        return str(size or '').strip()
    return str(int(v)) if float(v).is_integer() else str(v)


def _npd_token(npd: float) -> str:
    """Build a deterministic, filesystem-safe token for an NPD (used in ICC codes)."""
    if float(npd).is_integer():
        return f'N{int(npd):04d}'
    return f'N{int(round(npd * 100)):04d}'   # 0.5 → N0050, 1.5 → N0150


def _enumerate_npds(size_from, size_to) -> list[float]:
    """Return the standard NPDs lying within [size_from, size_to].
    If only one bound is given, returns [that value]; if neither, returns []."""
    a = _to_float_npd(size_from)
    b = _to_float_npd(size_to)
    if a is None and b is None:
        return []
    if a is not None and b is None:
        return [a]
    if a is None and b is not None:
        return [b]
    lo, hi = min(a, b), max(a, b)
    out = [n for n in STD_NPDS_INCHES if lo <= n <= hi]
    if a not in out:
        out.append(a)
    if b not in out:
        out.append(b)
    return sorted(set(out))


def _fmt_temperature(t):
    if t in (None, ''):
        return ''
    try:
        return f'{float(t):g}C'
    except (TypeError, ValueError):
        return str(t)


def _fmt_pressure(p):
    if p in (None, ''):
        return ''
    try:
        return f'{float(p):g}barg'
    except (TypeError, ValueError):
        return str(p)


def _spec_name(cls) -> str:
    """Stable SpecName for a PipingClass (matches SmartPlant SpecName key)."""
    return (cls.class_full_code or cls.class_code or 'SPEC').strip()


def _safe_class_token(cls) -> str:
    """Filesystem-safe token derived from class code (for ICC generation)."""
    raw = (cls.class_code or cls.class_full_code or 'X').strip()
    return re.sub(r'[^A-Za-z0-9]+', '', raw).upper() or 'X'


def _normalize_end_prep(text: str | None) -> str:
    """Map end-connection text → S3D EndPreparation enum (returns enum or text)."""
    if not text:
        return ''
    key = str(text).strip().lower()
    if key in _END_PREP_LOOKUP:
        return _END_PREP_LOOKUP[key]
    first = key.split()[0] if key.split() else ''
    return _END_PREP_LOOKUP.get(first, text)


def _normalize_pressure_class(rating: str | None) -> str:
    """Convert '150#', 'Class 300', 'ASME 900#' → numeric flange class string."""
    if not rating:
        return ''
    m = re.search(r'(\d+)', str(rating))
    return m.group(1) if m else str(rating)


# ─────────────────────────────────────────────────────────────────────────────
# 4. CAT SHEET DEFAULTS  (one entry per CAT data sheet)
#
# Keys per sheet:
#   commodity_type  — S3D CommodityType token (PIPE, FWN, FBLD, E90, T, ...)
#   geometry_type   — S3D GeometryType code
#   symbol_def      — Hexagon SP3D content path used by the symbol library
#   icc_prefix      — Industry-Commodity-Code prefix used to name parts
#   ports           — number of piping points (1, 2 or 3)
#   bend_angle      — Optional, used for elbow sheets
#   primary_label   — '' or ':Primary' suffix for Npd column name
# ─────────────────────────────────────────────────────────────────────────────
CAT_SHEET_DEFAULTS = {
    'PipeStock':         dict(commodity_type='PIPE',  geometry_type=None,  symbol_def=None,
                              icc_prefix='RAD_PIP', ports=2, primary_label=':Primary'),
    'WeldNeckFlange':    dict(commodity_type='FWN',   geometry_type='15',  symbol_def='Flange,Ingr.SP3D.Content.Piping.Flange',
                              icc_prefix='RAD_FWN', ports=2),
    'BlindFlange':       dict(commodity_type='FBLD',  geometry_type='220', symbol_def='BlindFlange,Ingr.SP3D.Content.Piping.BlindFlange',
                              icc_prefix='RAD_FBL', ports=1),
    '90DegElbow':        dict(commodity_type='E90',   geometry_type='20',  symbol_def='90DegreeElbow,Ingr.SP3D.Content.Piping.Elbow90Degree',
                              icc_prefix='RAD_E90', ports=2, bend_angle='90deg'),
    '90DegLRElbow':      dict(commodity_type='E90LR', geometry_type='20',  symbol_def='90DegreeElbow,Ingr.SP3D.Content.Piping.Elbow90Degree',
                              icc_prefix='RAD_E9L', ports=2, bend_angle='90deg'),
    '45DegElbow':        dict(commodity_type='E45',   geometry_type='20',  symbol_def='45DegreeElbow,Ingr.SP3D.Content.Piping.Elbow45Deg',
                              icc_prefix='RAD_E45', ports=2, bend_angle='45deg'),
    '45DegLRElbow':      dict(commodity_type='E45LR', geometry_type='20',  symbol_def='45DegreeElbow,Ingr.SP3D.Content.Piping.Elbow45Deg',
                              icc_prefix='RAD_E4L', ports=2, bend_angle='45deg'),
    'Tee':               dict(commodity_type='T',     geometry_type='75',  symbol_def='Tee,Ingr.SP3D.Content.Piping.Tee',
                              icc_prefix='RAD_TEE', ports=3, primary_label=':Primary'),
    'ReducingTee':       dict(commodity_type='TR',    geometry_type='75',  symbol_def='Tee,Ingr.SP3D.Content.Piping.Tee',
                              icc_prefix='RAD_TER', ports=3, primary_label=':Primary'),
    'ConcentricSwage':   dict(commodity_type='SWGC',  geometry_type='70',  symbol_def='ConcentricReducer,Ingr.SP3D.Content.Piping.Concentric',
                              icc_prefix='RAD_SWG', ports=2),
    'ConcentricReducer': dict(commodity_type='REDC',  geometry_type='70',  symbol_def='ConcentricReducer,Ingr.SP3D.Content.Piping.Concentric',
                              icc_prefix='RAD_REC', ports=2),
    'EccentricReducer':  dict(commodity_type='REDE',  geometry_type='65',  symbol_def='EccentricReducer,Ingr.SP3D.Content.Piping.Eccentric',
                              icc_prefix='RAD_REE', ports=2),
    'Cap':               dict(commodity_type='CAP',   geometry_type='220', symbol_def='Cap,Ingr.SP3D.Content.Piping.Cap',
                              icc_prefix='RAD_CAP', ports=1),
    'Weldolet':          dict(commodity_type='WOL',   geometry_type='15',  symbol_def='Weldolet,Ingr.SP3D.Content.Piping.Olet',
                              icc_prefix='RAD_WOL', ports=2),
    'GateValve':         dict(commodity_type='GAT',   geometry_type='15',  symbol_def='GateValve,Ingr.SP3D.Content.Piping.GateValve',
                              icc_prefix='RAD_GAT', ports=2),
    'GlobeValve':        dict(commodity_type='GLO',   geometry_type='15',  symbol_def='GlobeValve,Ingr.SP3D.Content.Piping.GlobeValveF',
                              icc_prefix='RAD_GLO', ports=2),
    'CheckValve':        dict(commodity_type='CHK',   geometry_type='15',  symbol_def='CheckValve,Ingr.SP3D.Content.Piping.CheckValve',
                              icc_prefix='RAD_CHK', ports=2),
    'Paddle':            dict(commodity_type='BLSPA', geometry_type='15',  symbol_def='PaddleSpacer,Ingr.SP3D.Content.Piping.PaddleSpacer',
                              icc_prefix='RAD_PAD', ports=2),
    'Coupling':          dict(commodity_type='CPL',   geometry_type='15',  symbol_def='Coupling,Ingr.SP3D.Content.Piping.Coupling',
                              icc_prefix='RAD_CPL', ports=2),
    'Nipple':            dict(commodity_type='NIP',   geometry_type='15',  symbol_def='Nipple,Ingr.SP3D.Content.Piping.Nipple',
                              icc_prefix='RAD_NIP', ports=2),
    'GasketPartData':    dict(commodity_type=None,    geometry_type=None,  symbol_def=None,
                              icc_prefix='RAD_GSK', ports=0),
    'BoltPartData':      dict(commodity_type=None,    geometry_type=None,  symbol_def=None,
                              icc_prefix='RAD_BLT', ports=0),
}


# ─────────────────────────────────────────────────────────────────────────────
# 5. SPEC SHEET BUILDERS
#
# Each builder is `(piping_class) → list[dict[column_name → value]]`.
# Unmapped column-name keys are silently dropped by the writer.
# ─────────────────────────────────────────────────────────────────────────────
def _rows_piping_materials_class_data(cls):
    return [{
        'SpecName':                       _spec_name(cls),
        'MaterialsOfConstructionClass':   cls.material_grade or '',
        'MaterialsDescription':           cls.material_grade or '',
        'FluidService':                   '; '.join(cls.service_list or []) or '',
        'DesignStandard':                 _normalize_pressure_class(cls.pressure_rating),
        'GasketRequirementOverride':      '1',
        'PipingNote1':                    (cls.raw_notes or '')[:255],
        'Comments':                       (cls.raw_notes or '')[:255],
        'RevisionNumber':                 '0',
        'PipingSpecStatus':               'Issued',
    }]


def _rows_service_limits(cls):
    rows = []
    for pt in (cls.pt_rating_table or []):
        rows.append({
            'SpecName':    _spec_name(cls),
            'Temperature': _fmt_temperature(pt.get('temperature_c')),
            'Pressure':    _fmt_pressure(pt.get('pressure_bar_g')),
        })
    return rows


def _rows_corrosion_allowance(cls):
    if not cls.corrosion_allowance:
        return []
    return [{
        'SpecName':            _spec_name(cls),
        'CorrosionAllowance':  cls.corrosion_allowance,
    }]


def _rows_pipe_nominal_diameters(cls):
    """One row per *unique* pipe NPD encountered across the class's components."""
    seen: set[float] = set()
    for c in cls.components.all():
        for sz in (c.size_from, c.size_to):
            v = _to_float_npd(sz)
            if v is not None:
                seen.add(v)
    return [
        {'SpecName': _spec_name(cls),
         'Npd': _normalize_npd(v),
         'NpdUnitType': S3D_DEFAULTS['NpdUnitType']}
        for v in sorted(seen)
    ]


def _rows_piping_commodity_matl_control_data(cls):
    """One row per (component × NPD) — emits an IndustryCommodityCode for traceability."""
    rows = []
    cls_token = _safe_class_token(cls)
    for c in cls.components.all().order_by('display_order'):
        sheet = route_component_to_cat_sheet(c)
        prefix = (CAT_SHEET_DEFAULTS.get(sheet, {}) or {}).get('icc_prefix', 'RAD_CMP')
        npds = _enumerate_npds(c.size_from, c.size_to)
        if not npds:
            continue
        for npd in npds:
            icc = f'{prefix}_{cls_token}_{_npd_token(npd)}'
            rows.append({
                'ContractorCommodityCode':   icc,
                'IndustryCommodityCode':     icc,
                'FirstSizeFrom':             _normalize_npd(npd),
                'FirstSizeTo':               _normalize_npd(npd),
                'FirstSizeUnits':            S3D_DEFAULTS['NpdUnitType'],
                'ShortMaterialDescription':  (c.description or c.sub_type or c.component_type)[:60],
                'LongMaterialDescription':   c.description or '',
                'FabricationType':           'Shop',
                'SupplyResponsibility':      'Contractor',
                'ReportingType':             'Each',
                'QuantityOfReportableParts': '1',
                'PipingNote1':               (c.notes or '')[:255],
            })
    return rows


def _rows_gasket_selection_filter(cls):
    rows = []
    for c in cls.components.filter(component_type='gasket'):
        rows.append({
            'SpecName':            _spec_name(cls),
            'NominalDiameterFrom': _normalize_npd(c.size_from),
            'NominalDiameterTo':   _normalize_npd(c.size_to),
            'NpdUnitType':         S3D_DEFAULTS['NpdUnitType'],
            'GasketOption':        '1',
            'PressureRating':      _normalize_pressure_class(cls.pressure_rating),
            'EndPreparation':      _normalize_end_prep(c.end_connection),
            'EndStandard':         S3D_DEFAULTS['EndStandard'],
            'Priority':            '1',
            'Comments':            (c.description or '')[:128],
        })
    return rows


def _rows_bolt_selection_filter(cls):
    rows = []
    for c in cls.components.filter(component_type='bolt'):
        rows.append({
            'SpecName':            _spec_name(cls),
            'NominalDiameterFrom': _normalize_npd(c.size_from),
            'NominalDiameterTo':   _normalize_npd(c.size_to),
            'NpdUnitType':         S3D_DEFAULTS['NpdUnitType'],
            'BoltOption':          '1',
            'PressureRating':      _normalize_pressure_class(cls.pressure_rating),
            'EndPreparation':      _normalize_end_prep(c.end_connection) or S3D_DEFAULTS['EndPrep_RF'],
            'EndStandard':         S3D_DEFAULTS['EndStandard'],
            'Priority':            '1',
            'Comments':            (c.description or '')[:128],
        })
    return rows


# Sheet → list of builders. Each builder yields zero-or-more rows per class.
SPEC_SHEET_BUILDERS = {
    'PipingMaterialsClassData':        [_rows_piping_materials_class_data],
    'ServiceLimits':                   [_rows_service_limits],
    'CorrosionAllowance':              [_rows_corrosion_allowance],
    'PipeNominalDiameters':            [_rows_pipe_nominal_diameters],
    'PipingCommodityMatlControlData':  [_rows_piping_commodity_matl_control_data],
    'GasketSelectionFilter':           [_rows_gasket_selection_filter],
    'BoltSelectionFilter':             [_rows_bolt_selection_filter],
}


# ─────────────────────────────────────────────────────────────────────────────
# 6. CAT — component routing
#
# Each rule: (component_type, regex_against_sub_type+description, target_sheet)
# First matching rule wins.  Matching is case-insensitive.
# Ordered narrow→generic (LR before generic, blind before weld-neck,
# reducing-tee before tee, ecc/conc reducers before swage).
# ─────────────────────────────────────────────────────────────────────────────
_CAT_ROUTING_RULES = [
    # Pipe ────────────────────────────────────────────────────────────────
    ('pipe',    r'.*',                                                            'PipeStock'),

    # Flange ──────────────────────────────────────────────────────────────
    ('flange',  r'(?i)\b(blind|bld|blnd)\b',                                      'BlindFlange'),
    ('flange',  r'(?i)\b(wn|weld[\s\-]?neck|weldneck)\b',                         'WeldNeckFlange'),
    ('flange',  r'.*',                                                            'WeldNeckFlange'),

    # Fitting ─────────────────────────────────────────────────────────────
    ('fitting', r'(?i)\b90.*long.*radius\b|\b90.*\bl[\s\.]*r\.?',                  '90DegLRElbow'),
    ('fitting', r'(?i)\b45.*long.*radius\b|\b45.*\bl[\s\.]*r\.?',                  '45DegLRElbow'),
    ('fitting', r'(?i)(elbow|ell)\W*90\b|\b90\W*deg.*(elbow|ell)\b|\b90\b.*elbow', '90DegElbow'),
    ('fitting', r'(?i)(elbow|ell)\W*45\b|\b45\W*deg.*(elbow|ell)\b|\b45\b.*elbow', '45DegElbow'),
    ('fitting', r'(?i)reducing\s*tee|red\.?\s*tee|red\b.*tee\b',                  'ReducingTee'),
    ('fitting', r'(?i)\b(tee|t-piece)\b',                                         'Tee'),
    ('fitting', r'(?i)concentric\s*reducer|conc\.?\s*red|conc\b.*reducer',        'ConcentricReducer'),
    ('fitting', r'(?i)eccentric\s*reducer|ecc\.?\s*red|ecc\b.*reducer',           'EccentricReducer'),
    ('fitting', r'(?i)\bswage|swg\b',                                             'ConcentricSwage'),
    ('fitting', r'(?i)\bcap\b',                                                   'Cap'),
    ('fitting', r'(?i)weldolet|wol\b|sockolet|sol\b|thredolet|tol\b|olet',        'Weldolet'),
    ('fitting', r'(?i)coupling',                                                  'Coupling'),
    ('fitting', r'(?i)nipple',                                                    'Nipple'),
    ('fitting', r'(?i)paddle|spec\s*blind|spectacle|spacer\s*ring',               'Paddle'),
    # No fitting fallback — leave un-routable fittings in the unrouted log.

    # Valve ───────────────────────────────────────────────────────────────
    ('valve',   r'(?i)\bgate\b',                                                  'GateValve'),
    ('valve',   r'(?i)\b(globe|needle)\b',                                        'GlobeValve'),
    ('valve',   r'(?i)\bcheck\b|nrv|non[\s\-]?return',                            'CheckValve'),
    # No valve fallback.

    # Gasket / Bolt ───────────────────────────────────────────────────────
    ('gasket',  r'.*',                                                            'GasketPartData'),
    ('bolt',    r'.*',                                                            'BoltPartData'),
]


def _bucket_component_type(component) -> str:
    """Collapse free-text `component_type` (e.g. 'elbow 90 deg lr',
    'slip on flange', 'flanges blind') into a canonical bucket so routing rules
    can match.  Order matters: more specific keywords first."""
    blob = f'{(component.component_type or "")} {(component.sub_type or "")} {(component.description or "")}'.lower()
    # gasket / bolt first (avoid 'bolt-on flange' confusing flange match)
    if any(k in blob for k in ('gasket', 'gskt')):
        return 'gasket'
    if any(k in blob for k in ('bolt', 'stud bolt', 'machine bolt')) and 'bolted' not in blob:
        return 'bolt'
    if any(k in blob for k in ('valve', 'nrv', 'non-return', 'non return')):
        return 'valve'
    if any(k in blob for k in ('flange', 'flng')):
        return 'flange'
    if any(k in blob for k in ('elbow', 'ell ', ' tee', 'reducer', 'reducing', 'cap', 'olet',
                                'coupling', 'nipple', 'swage', 'paddle', 'spec blind',
                                'spectacle', 'spacer', 'fitting', 'union')):
        return 'fitting'
    if any(k in blob for k in ('pipe', 'tube')):
        return 'pipe'
    return (component.component_type or '').strip().lower()


def route_component_to_cat_sheet(component) -> str | None:
    """Return the CAT sheet name for a component, or None if no rule matches."""
    bucket = _bucket_component_type(component)
    sub_text = f'{component.component_type or ""} {component.sub_type or ""} {component.description or ""}'
    for ctype, pattern, sheet in _CAT_ROUTING_RULES:
        if bucket != ctype:
            continue
        if re.search(pattern, sub_text):
            return sheet
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 7. CAT BUILDERS — per-component, multi-NPD row generators
#
#  Each builder returns `list[dict]` (one dict per generated row) so the
#  exporter can emit 1..N rows per component (one per NPD in range).
# ─────────────────────────────────────────────────────────────────────────────
def _common_part_row(cls, comp, sheet_name, npd1, npd2=None, npd3=None) -> dict:
    """Shared field set for all CAT 'commodity part' sheets.

    Includes BOTH bracketed (`Npd[1]`) and bracket+suffixed (`Npd[1]:Primary`)
    column-name variants — only whichever matches the template's actual header
    is written; the rest are silently dropped by the writer."""
    d = CAT_SHEET_DEFAULTS.get(sheet_name, {}) or {}
    cls_token = _safe_class_token(cls)
    suffix_parts = [_npd_token(n) for n in (npd1, npd2, npd3) if n is not None]
    icc = f'{d.get("icc_prefix", "RAD_CMP")}_{cls_token}_{"_".join(suffix_parts)}'
    end_prep = _normalize_end_prep(comp.end_connection) or S3D_DEFAULTS['EndPrep_BW']
    sched = comp.schedule_or_rating or 'STD'
    pressure = _normalize_pressure_class(cls.pressure_rating)
    primary_lbl = d.get('primary_label', '')

    row: dict = {
        'IndustryCommodityCode':         icc,
        'CommodityType':                 d.get('commodity_type') or '',
        'GeometryType':                  d.get('geometry_type') or '',
        'GraphicalRepresentationOrNot':  S3D_DEFAULTS['GraphicalRep'],
        'SymbolDefinition':              d.get('symbol_def') or '',
        'MaterialGrade':                 cls.material_grade or '',
        'GeometricIndustryStandard':     comp.material_standard or '',
        # Port 1 (always present)
        'PipingPointBasis[1]':           S3D_DEFAULTS['PipingPointBasis'],
        'PressureRating[1]':             pressure,
        'EndPreparation[1]':             end_prep,
        'EndStandard[1]':                S3D_DEFAULTS['EndStandard'],
        'ScheduleThickness[1]':          sched,
        'FlowDirection[1]':              S3D_DEFAULTS['FlowDirection_Bidir'],
        'Npd[1]':                        _normalize_npd(npd1),
        f'Npd[1]{primary_lbl}':          _normalize_npd(npd1),
        'NpdUnitType[1]':                S3D_DEFAULTS['NpdUnitType'],
        'PipingNote1':                   (comp.notes or '')[:255],
    }
    if d.get('bend_angle'):
        row['BendAngle'] = d['bend_angle']

    # Port 2 (if applicable)
    if npd2 is not None and d.get('ports', 1) >= 2:
        row.update({
            'PipingPointBasis[2]':       S3D_DEFAULTS['PipingPointBasis'],
            'PressureRating[2]':         pressure,
            'EndPreparation[2]':         end_prep,
            'EndStandard[2]':            S3D_DEFAULTS['EndStandard'],
            'ScheduleThickness[2]':      sched,
            'FlowDirection[2]':          S3D_DEFAULTS['FlowDirection_Bidir'],
            'Npd[2]':                    _normalize_npd(npd2),
            f'Npd[2]{primary_lbl}':      _normalize_npd(npd2),
            'NpdUnitType[2]':            S3D_DEFAULTS['NpdUnitType'],
        })

    # Port 3 (Tee / ReducingTee — secondary branch)
    if npd3 is not None and d.get('ports', 1) >= 3:
        row.update({
            'PipingPointBasis[3]':       S3D_DEFAULTS['PipingPointBasis'],
            'PressureRating[3]':         pressure,
            'EndPreparation[3]':         end_prep,
            'EndStandard[3]':            S3D_DEFAULTS['EndStandard'],
            'ScheduleThickness[3]':      sched,
            'FlowDirection[3]':          S3D_DEFAULTS['FlowDirection_Bidir'],
            'Npd[3]':                    _normalize_npd(npd3),
            'Npd[3]:Secondary':          _normalize_npd(npd3),
            'NpdUnitType[3]':            S3D_DEFAULTS['NpdUnitType'],
        })

    # Single-port shapes (BlindFlange, Cap) → unidirectional flow.
    if d.get('ports', 1) == 1:
        row['FlowDirection[1]'] = S3D_DEFAULTS['FlowDirection_OutOnly']

    return row


def _build_cat_rows(cls, comp, sheet_name) -> list[dict]:
    """Multi-NPD row generator used by every commodity-part CAT sheet."""
    d = CAT_SHEET_DEFAULTS.get(sheet_name, {}) or {}
    ports = d.get('ports', 1)
    npds = _enumerate_npds(comp.size_from, comp.size_to)
    if not npds:
        return []
    rows: list[dict] = []
    for npd in npds:
        if ports == 1:
            rows.append(_common_part_row(cls, comp, sheet_name, npd))
        elif ports == 2:
            rows.append(_common_part_row(cls, comp, sheet_name, npd, npd))
        elif ports == 3:
            # Tee: run × run × branch.  When branch size is not separately
            # captured, default branch = run (gets refined by extractor later).
            rows.append(_common_part_row(cls, comp, sheet_name, npd, npd, npd))
    return rows


def _build_gasket_part_rows(cls, comp) -> list[dict]:
    d = CAT_SHEET_DEFAULTS['GasketPartData']
    cls_token = _safe_class_token(cls)
    npds = _enumerate_npds(comp.size_from, comp.size_to)
    if not npds:
        return []
    rows = []
    for npd in npds:
        icc = f'{d["icc_prefix"]}_{cls_token}_{_npd_token(npd)}'
        rows.append({
            'IndustryCommodityCode':  icc,
            'NominalDiameterFrom':    _normalize_npd(npd),
            'NominalDiameterTo':      _normalize_npd(npd),
            'NominalDiameter':        _normalize_npd(npd),
            'NpdUnitType':            S3D_DEFAULTS['NpdUnitType'],
            'GasketType':             comp.sub_type or 'SpiralWound',
            'GasketIndustryStandard': comp.material_standard or 'ASME B16.20',
            'MaterialsGrade':         cls.material_grade or '',
            'FlangeFacing':           cls.flange_facing or 'RF',
            'MaximumPressure':        _normalize_pressure_class(cls.pressure_rating),
        })
    return rows


def _build_bolt_part_rows(cls, comp) -> list[dict]:
    d = CAT_SHEET_DEFAULTS['BoltPartData']
    cls_token = _safe_class_token(cls)
    npds = _enumerate_npds(comp.size_from, comp.size_to)
    if not npds:
        return []
    rows = []
    for npd in npds:
        icc = f'{d["icc_prefix"]}_{cls_token}_{_npd_token(npd)}'
        rows.append({
            'IndustryCommodityCode':     icc,
            'BoltType':                  comp.sub_type or 'Stud',
            'GeometricIndustryStandard': comp.material_standard or 'ASME B16.5',
            'MaterialsGrade':            cls.material_grade or '',
            'CoatingType':               '0',
        })
    return rows


# Sheet → builder.  All commodity-part sheets share the same multi-NPD builder;
# gasket and bolt sheets have specialised builders.
def _make_cat_builder(sheet_name):
    return lambda cls, comp: _build_cat_rows(cls, comp, sheet_name)


CAT_SHEET_BUILDERS = {sn: _make_cat_builder(sn) for sn in CAT_SHEET_DEFAULTS
                      if sn not in ('GasketPartData', 'BoltPartData')}
CAT_SHEET_BUILDERS['GasketPartData'] = _build_gasket_part_rows
CAT_SHEET_BUILDERS['BoltPartData']   = _build_bolt_part_rows
