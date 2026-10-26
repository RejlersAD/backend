"""§6.8 — vendor IO database auto-fill.

Match IO-list TAG_Number to vendor TAG_Number (both uppercase, spaces
removed). Only vendor rows with Status = Live are used. Only EMPTY IO-list
cells are written. Conflicts (both filled, values differ) are reported for
the soft-coded conflict fields; service wording differences are listed as
text drift, never as conflicts.

Vendor headers carry a leading ordinal (`30. RTP/FTALocation`) which is
stripped. DCS and ESD databases use slightly different spellings for the
FTA/IOLINK columns; COLUMN_MAP covers both.
"""
import re
from dataclasses import dataclass, field

import openpyxl

_VENDOR_SHEET = 'IO_DATABASE'


def _norm_header(h):
    h = re.sub(r'\s+', ' ', str(h or '').replace('\n', ' ')).strip()
    return re.sub(r'^\d+\.\s*', '', h)          # strip "30. " ordinals


def _norm_tag(t):
    return re.sub(r'\s+', '', str(t or '')).upper()


# IO list column ← vendor column candidates (DCS spelling, then ESD)
COLUMN_MAP = {
    'Multi_Pair_Cable_Name': ('Multi_Pair_Cable_Name',),
    'Multi_Pair_Cable_Type': ('Multi_Pair_Cable_Type',),
    'Multi_Pair_Cable_Size': ('Multi_Pair_Cable_Size',),
    'Multi_Pair_Cable_Pair': ('Multi_Pair_Cable_Pair',),
    'MR_Cabinet': ('MR_Cabinet',),
    'MR_Cabinet_TB': ('MR_Cabinet_TB',),
    'Interconnecting_Cable_Name': ('Interconnecting_Cable_Name',),
    'Interconnecting_Cable_Type': ('Interconnecting_Cable_Type',),
    'Interconnecting_Cable_Size': ('Interconnecting_Cable_Size',),
    'Interconnecting_Cable_Pair': ('Interconnecting_Cable_Pair',),
    'Interconnecting_MR_Cabinet': ('Interconnecting_MR_Cabinet',),
    'Interconnecting_MR_Cabinet_TB': ('Interconnecting_MR_Cabinet_TB',),
    'RTP/FTA Location': ('RTP/FTALocation', 'RTP/FTA_Location'),
    'RTP/FTA Channel': ('RTP/FTAChannel', 'RTP/FTA_Channel'),
    'RTP/FTA-TM1': ('RTP/FTA-TM1', 'RTP/FTA_TM1'),
    'RTP/FTA-TM2': ('RTP/FTA-TM2', 'RTP/FTA_TM2'),
    'RTP/FTA_Barrier Type': ('RTP/FTA_BarrierType', 'RTP/FTA_Barrier_Type'),
    'System_Cabinet': ('System_Cabinet',),
    'SIDE': ('SIDE',),
    'IOLINK1_HWL_RACK': ('IOLINK1_HWL_RACK',),
    'IOLINK ADDR_HWL_POSITION': ('IOLINKADDR_HWL_POSITION', 'IOLINK_ADDR_HWL_POSITION'),
    'IOPNAME': ('IOPNAME',),
    'IO_CHANNEL': ('IO_CHANNEL',),
    'Loc': ('Loc',),
    'POS': ('POS',),
    'Controller': ('Controller',),
    # NOTE: Location stays compare-only — the reference baseline shows the
    # vendor Location column uses a different coding scheme (CA82/SR80
    # marshalling areas vs plant locations), so auto-filling it wrote 288
    # cells of the wrong semantics on the reference data.
}

# Both-filled differences on these are engineering conflicts; Service
# differences are text drift instead.
CONFLICT_FIELDS = ('IO_Type_Control_System', 'MR_Cabinet', 'Multi_Pair_Cable_Name',
                   'Multi_Pair_Cable_Pair', 'Location', 'Certification', 'System')

# Compare-only columns (never auto-filled — spec §6.8 table note "compare
# when both filled"): same column name on both sides.
COMPARE_ONLY = ('IO_Type_Control_System', 'Certification', 'Location')


_EMPTY_MARKERS = ('', '-', 'N/A', 'NA', 'NONE')


def _is_empty(value):
    return value is None or str(value).strip().upper() in _EMPTY_MARKERS


def _norm_pair(value):
    """`Pr 02` == 2 == `PR2` — cable pair conflicts compare the NUMBER."""
    m = re.search(r'\d+', str(value or ''))
    return int(m.group(0)) if m else None


def _values_equal(field, a, b):
    if _is_empty(a) or _is_empty(b):
        return True                     # an empty side never conflicts
    if field == 'Multi_Pair_Cable_Pair':
        pa, pb = _norm_pair(a), _norm_pair(b)
        return pa is not None and pa == pb
    # spacing inside values is not significant (`PSA2- ANALYSER` ==
    # `PSA2-ANALYSER`, `13KC701JL31-D-2- 03` == `13KC701JL31-D-2-03`)
    return re.sub(r'\s+', '', str(a)).upper() == re.sub(r'\s+', '', str(b)).upper()


@dataclass
class VendorDB:
    name: str                 # file name, e.g. 13-IE-166-503070040_Q1
    system: str               # 'DCS' | 'ESD' (guessed from content/file)
    by_tag: dict = field(default_factory=dict)   # norm tag → row dict
    live_count: int = 0


def read_vendor_db(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if _VENDOR_SHEET not in wb.sheetnames:
            return {'error': f'Sheet {_VENDOR_SHEET} not found.'}
        ws = wb[_VENDOR_SHEET]
        rows = list(ws.iter_rows(values_only=True))
        header_idx = None
        headers = []
        for i, row in enumerate(rows[:10]):
            if any(_norm_header(c) == 'TAG_Number' for c in row if c):
                header_idx = i
                headers = [_norm_header(c) for c in row]
                break
        if header_idx is None:
            return {'error': 'No TAG_Number header row found in IO_DATABASE.'}
        by_tag = {}
        live = 0
        for row in rows[header_idx + 1:]:
            rec = {headers[i]: (row[i] if i < len(row) else None)
                   for i in range(len(headers)) if headers[i]}
            if str(rec.get('Status') or '').strip() != 'Live':
                continue
            tag = _norm_tag(rec.get('TAG_Number'))
            if not tag:
                continue
            live += 1
            by_tag.setdefault(tag, rec)     # first Live row wins
        fname = path.replace('\\', '/').split('/')[-1]
        m = re.match(r'([0-9A-Z-]+?)_([A-Z0-9]+)\.xlsx$', fname, re.IGNORECASE)
        return VendorDB(name=m.group(1) if m else fname, system='', by_tag=by_tag, live_count=live)
    finally:
        wb.close()


def _vendor_value(rec, candidates):
    for name in candidates:
        v = rec.get(name)
        if v is not None and str(v).strip() != '':
            return str(v).strip()
    return None


def autofill(baseline_rows, vendor_dbs):
    """Match every baseline row against the vendor DBs.

    Returns (fills, conflicts, no_record) where fills[tag] = {column: value},
    conflicts = [{tag, field, io_list, vendor, type}], no_record = [tag].
    Vendor databases are tried in order (DCS first, then ESD; GDS rows live
    in the DCS database per the reference data)."""
    fills, conflicts, no_record = {}, [], []
    for rec in baseline_rows:
        tag = rec.get('_tag_norm')
        if not tag:
            continue
        vrow = None
        source = None
        for db in vendor_dbs:
            if tag in db.by_tag:
                vrow = db.by_tag[tag]
                source = db.name
                break
        if vrow is None:
            no_record.append(tag)
            continue
        row_fills = {}
        for io_col, candidates in COLUMN_MAP.items():
            v = _vendor_value(vrow, candidates)
            if v is None:
                continue
            existing = rec.get(io_col)
            if _is_empty(existing):
                row_fills[io_col] = v
            elif io_col in CONFLICT_FIELDS and not _values_equal(io_col, existing, v):
                conflicts.append({
                    'tag': tag, 'field': io_col,
                    'io_list': str(existing).strip(), 'vendor': v,
                    'type': 'engineering',
                })
        # Compare-only fields: never written, only flagged when both filled
        for field in COMPARE_ONLY:
            v = _vendor_value(vrow, (field,))
            existing = rec.get(field)
            if v and not _is_empty(existing):
                if not _values_equal(field, existing, v):
                    conflicts.append({'tag': tag, 'field': field,
                                      'io_list': str(existing).strip(), 'vendor': v,
                                      'type': 'engineering'})
        # System conflict: vendor System column vs IO list System
        v_sys = _vendor_value(vrow, ('System',))
        io_sys = str(rec.get('System') or '').strip()
        if v_sys and io_sys and not _values_equal('System', io_sys, v_sys):
            conflicts.append({'tag': tag, 'field': 'System', 'io_list': io_sys,
                              'vendor': v_sys, 'type': 'engineering'})
        # Service wording drift is listed separately, never a conflict
        v_service = _vendor_value(vrow, ('Service',))
        io_service = str(rec.get('Service') or '').strip()
        if (v_service and io_service
                and not _values_equal('Service', io_service, v_service)):
            conflicts.append({'tag': tag, 'field': 'Service', 'io_list': io_service,
                              'vendor': v_service, 'type': 'text-drift'})
        if row_fills:
            fills[tag] = {'cells': row_fills, 'source': source}
    return fills, conflicts, no_record
