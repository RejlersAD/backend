"""Standard Template export for an IOListExtraction (replaces §7.6 / §10.4).

The instrumentation team's Standard_Template.xlsx is LOADED and filled —
the workbook is never rebuilt from scratch. Sheet 1 is the cover, sheet 2
the 53-column data sheet (row 1 = column numbers, row 2 = headers, data
from row 3). openpyxl cannot round-trip the multi-line page header (it
writes _x000a_), so the data sheet's <headerFooter> is rewritten directly
in the saved package (see _set_header).
"""
import io
import re
import zipfile
from copy import copy
from xml.sax.saxutils import escape

import openpyxl

COVER_SHEET = 'IO List for DCS, GDS & ESD'
DATA_SHEET = 'Template'
FIRST_ROW = 3                    # row 1 = column numbers, row 2 = headers
COVER_CELLS = ('I3', 'J4', 'L4', 'I6', 'I8')

# The 53 data-sheet headers, in template order. Names with a line break are
# written here with \n exactly as they appear in row 2 of the template.
DATA_HEADERS = [
    'Loop_Name', 'TAG_Number', 'Instrument_Type\nName',
    'Instrument_Type\nDescription', 'Service', 'P&ID', 'Tag_Status',
    'Supplier', 'Location', 'System', 'IO_Type_Name',
    'IO_Type_Control_System', 'Certification', 'SIL_Classification',
    'DCS_Range_Min', 'DCS_Range_Max', 'DCS_Range_Unit',
    'Low_Alarm_Value', 'Low_Low_Alarm_Value', 'High_Alarm_Value',
    'High_High_Alarm_Value',
    'JB/Shutdown_Console_Name', 'Shutdown_Console_TB',
    'Multi_Pair_Cable_Name', 'Multi_Pair_Cable_Type', 'Multi_Pair_Cable_Size',
    'Multi_Pair_Cable_Pair', 'MR_Cabinet', 'MR_Cabinet_TB',
    'Interconnecting_Cable_Name', 'Interconnecting_Cable_Type',
    'Interconnecting_Cable_Size', 'Interconnecting_Cable_Pair',
    'Interconnecting_MR_Cabinet', 'Interconnecting_MR_Cabinet_TB',
    'RTP/FTA Location', 'RTP/FTA Channel', 'RTP/FTA-TM1', 'RTP/FTA-TM2',
    'RTP/FTA_Barrier Type', 'System_Cabinet', 'SIDE', 'IOLINK1_HWL_RACK',
    'IOLINK ADDR_HWL_POSITION', 'IOPNAME', 'IO_CHANNEL', 'Loc', 'POS',
    'Controller', 'Remark 4', 'Unit_Name', 'Area_Name', 'Rev',
]

# Set-point letter → alarm column (as decided by the extraction).
_ALARM_COLUMN_BY_LETTER = {
    'H': 'High_Alarm_Value', 'SH': 'High_Alarm_Value',
    'HH': 'High_High_Alarm_Value', 'SHH': 'High_High_Alarm_Value',
    'L': 'Low_Alarm_Value', 'SL': 'Low_Alarm_Value',
    'LL': 'Low_Low_Alarm_Value', 'SLL': 'Low_Low_Alarm_Value',
}


def _norm_header(h):
    return re.sub(r'\s+', ' ', str(h or '').replace('\n', ' ')).strip()


def validate_template(data):
    """Upload-time check. Returns {'ok', 'missing', 'columns', 'name'}:
    the two sheets exist, row 2 of the data sheet holds the 53 header names
    and the cover cells I3 / J4 / L4 / I6 / I8 exist."""
    missing = []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data))
    except Exception:
        return {'ok': False, 'missing': ['a readable .xlsx workbook'], 'columns': 0}
    if COVER_SHEET not in wb.sheetnames:
        missing.append(f'sheet {COVER_SHEET}')
    if DATA_SHEET not in wb.sheetnames:
        missing.append(f'sheet {DATA_SHEET}')
    columns = 0
    if DATA_SHEET in wb.sheetnames:
        ws = wb[DATA_SHEET]
        headers = [_norm_header(ws.cell(2, c).value) for c in range(1, ws.max_column + 1)]
        columns = len([h for h in headers if h])
        for h in DATA_HEADERS:
            if _norm_header(h) not in headers:
                missing.append(f'header {_norm_header(h)} missing')
    if COVER_SHEET in wb.sheetnames:
        cover = wb[COVER_SHEET]
        for addr in COVER_CELLS:
            try:
                cover[addr]
            except Exception:
                missing.append(f'cover cell {addr}')
    wb.close()
    return {'ok': not missing, 'missing': missing, 'columns': columns}


def pad_tag(tag, width):
    """'13-AI-7201B' → '13-AI   -7201B' when the project uses the padded
    (Honeywell) tag format. Applied in the export only — the app keeps
    storing and matching normalised tags. The unit part is any 1–4 char
    alphanumeric code, so non-reference projects pad correctly too."""
    m = re.match(r'^([0-9A-Z]{1,4})-([A-Z]+)\s*-(.+?)\s*$', (tag or '').strip().upper())
    if not m or not width:
        return tag
    return f"{m.group(1)}-{m.group(2).ljust(width)}-{m.group(3)}"


def detect_tag_padding(tags):
    """Most common length of the function part (letters + padding) across
    the baseline's TAG_Number values. None when no baseline / no match."""
    widths = {}
    for tag in tags:
        m = re.match(r'^[0-9A-Z]{1,4}-([A-Z]+\s*)-', str(tag or ''))
        if m:
            w = len(m.group(1))
            widths[w] = widths.get(w, 0) + 1
    if not widths:
        return None
    return max(widths.items(), key=lambda kv: kv[1])[0]


def _coerce_number(v):
    """baselineRows in the extraction payload are stringified; restore the
    numeric type so numbers stay numbers in the export."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    if re.fullmatch(r'-?\d+\.\d+', s):
        return float(s)
    return v


def _set_header(xlsx, sheet_title, left, center, right, footer='Page &P of &N'):
    """openpyxl cannot round-trip the template's multi-line page header (it
    writes _x000a_), so the data sheet's <headerFooter> is rewritten
    directly in the saved package."""
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    wbxml = src.read('xl/workbook.xml').decode()
    rels = src.read('xl/_rels/workbook.xml.rels').decode()
    rid = re.search(r'<sheet[^>]*name="%s"[^>]*r:id="([^"]+)"'
                    % re.escape(escape(sheet_title, {'"': '&quot;'})), wbxml).group(1)
    target = (re.search(r'Id="%s"[^>]*Target="([^"]+)"' % rid, rels)
              or re.search(r'Target="([^"]+)"[^>]*Id="%s"' % rid, rels))
    path = 'xl/' + target.group(1).lstrip('/').replace('xl/', '')
    hf = ('<headerFooter><oddHeader>' + escape('&L' + left + '&C' + center + '&R' + right)
          + '</oddHeader><oddFooter>' + escape(footer) + '</oddFooter></headerFooter>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == path:
                x = data.decode()
                x = re.sub(r'<headerFooter.*?</headerFooter>|<headerFooter/>', '', x, flags=re.S)
                # headerFooter must come after pageSetup / pageMargins and
                # before drawing / legacyDrawing / tableParts
                anchor = re.search(
                    r'<(rowBreaks|colBreaks|customProperties|cellWatches|ignoredErrors'
                    r'|smartTags|drawing|legacyDrawing|legacyDrawingHF|picture|oleObjects'
                    r'|controls|webPublishItems|tableParts|extLst)\b', x)
                pos = anchor.start() if anchor else x.rindex('</worksheet>')
                data = (x[:pos] + hf + x[pos:]).encode()
            dst.writestr(item, data)
    return out.getvalue()


def build_rows(extraction, template_headers, baseline_rows=None):
    """One dict per data row, keyed by the template's exact header names.

    Order: the baseline order first (as in the IO-list Rev), then new rows
    from the P&ID sorted by P&ID → loop → tag. Vendor fills write only
    cells empty in the baseline. Set points land in their alarm column by
    alarm letter. Returns (rows, sources) where sources lists
    {tag, column, value, source, sheet, vendorDoc} for the SOURCES sheet.
    """
    key = {h: _norm_header(h) for h in template_headers}
    vendor = extraction.vendor_payload or {}
    fills = vendor.get('fills') or {}

    if baseline_rows is None:
        baseline_rows = [
            {k: _coerce_number(v) for k, v in row.items()}
            for row in (vendor.get('baselineRows') or [])
        ]

    # set points per tag → alarm column (keyed by the normalised tag, as the
    # app stores and matches normalised tags everywhere)
    def _norm(t):
        return re.sub(r'\s+', '', str(t or '')).upper()

    sp_by_tag = {}
    for sp in extraction.setpoints_payload or []:
        if sp.get('value'):
            sp_by_tag.setdefault(_norm(sp['tag']), []).append(sp)

    rows, sources = [], []
    baseline_norms = set()
    for rec in baseline_rows:
        tag_norm = rec.get('_tag_norm', '')
        baseline_norms.add(tag_norm)
        fill = (fills.get(tag_norm) or {})
        cells = fill.get('cells', {})
        out = {}
        for h in template_headers:
            k = key[h]
            if h == 'TAG_Number':
                v = rec.get(k)
            elif k in cells:
                v = cells[k]
                sources.append({'tag': tag_norm, 'column': k, 'value': cells[k],
                                'source': 'Vendor DB', 'sheet': '',
                                'vendorDoc': fill.get('source', '')})
            else:
                v = rec.get(k)
            out[h] = v
        for sp in sp_by_tag.get(tag_norm, []):
            col = _ALARM_COLUMN_BY_LETTER.get(sp.get('point'), '') or sp.get('column', '')
            if col in out and not out[col]:
                out[col] = f"{sp['value']} {sp.get('unit', '')}".strip()
                sources.append({'tag': tag_norm, 'column': col, 'value': out[col],
                                'source': 'P&ID', 'sheet': sp.get('sheet', ''),
                                'vendorDoc': ''})
        rows.append(out)

    # new rows from the P&ID, sorted by P&ID → loop → tag
    def _pid_ref(t):
        doc = getattr(extraction, 'pid_document', None)
        return (doc.document_number if doc else '') or ''

    new_tags = [t for t in (extraction.tags_payload or [])
                if (t.get('ioClass') in ('AI', 'AO', 'DI', 'DO') or t.get('derived'))
                and t.get('norm') not in baseline_norms]
    new_tags.sort(key=lambda t: (_pid_ref(t), t.get('loopKey') or '', t.get('tag') or ''))
    current_rev = extraction.revision or ''
    for t in new_tags:
        out = {h: None for h in template_headers}
        for h in template_headers:
            k = key[h]
            if k == 'Loop_Name':
                out[h] = t.get('loopKey') or ''
            elif k == 'TAG_Number':
                out[h] = t.get('tag') or ''
            elif k == 'Instrument_Type Name':
                out[h] = t.get('ioType') or ''
            elif k == 'Service':
                out[h] = t.get('service') or ''
                if out[h]:
                    sources.append({'tag': t.get('norm'), 'column': k, 'value': out[h],
                                    'source': 'P&ID', 'sheet': (t.get('page') or 0) + 1,
                                    'vendorDoc': ''})
            elif k == 'P&ID':
                out[h] = _pid_ref(t)
            elif k == 'System':
                out[h] = t.get('system') or ''
            elif k == 'Tag_Status':
                out[h] = 'New'
            elif k == 'Rev':
                out[h] = current_rev
        for sp in sp_by_tag.get(_norm(t.get('norm') or t.get('tag')), []):
            col = _ALARM_COLUMN_BY_LETTER.get(sp.get('point'), '') or sp.get('column', '')
            if col in out and not out[col]:
                out[col] = f"{sp['value']} {sp.get('unit', '')}".strip()
                sources.append({'tag': t.get('norm'), 'column': col, 'value': out[col],
                                'source': 'P&ID', 'sheet': sp.get('sheet', ''),
                                'vendorDoc': ''})
        rows.append(out)
    return rows, sources


def fill_template(template_bytes, rows, cover, data_sheet_name=None, tag_pad_width=None):
    """Load the stored template, write one row per IO-list row from row 3 in
    the template's column order (matching each value by header name) and fill
    the cover. Returns the openpyxl workbook (only the two template sheets);
    callers may append review sheets and must finish with save_with_header."""
    wb = openpyxl.load_workbook(io.BytesIO(template_bytes))
    ws = wb[DATA_SHEET]
    headers = [ws.cell(2, c).value for c in range(1, ws.max_column + 1)]
    ncol = len(headers)
    style = [copy(ws.cell(FIRST_ROW, c)._style) for c in range(1, ncol + 1)]
    height = ws.row_dimensions[FIRST_ROW].height
    template_max_row = ws.max_row   # max_row rescans every cell — read once
    for i, row in enumerate(rows):
        r = FIRST_ROW + i
        for c, h in enumerate(headers, 1):
            v = row.get(h)
            if h == 'TAG_Number':
                v = pad_tag(v, tag_pad_width)
            if v in (None, ''):
                cell = ws.cell(r, c)
                cell.value = None
            else:
                cell = ws.cell(r, c, v)
            if r > template_max_row or cell.has_style is False:
                cell._style = copy(style[c - 1])
        ws.row_dimensions[r].height = height
    last = FIRST_ROW + max(len(rows), 1) - 1
    # clear formatted-but-empty template rows below the data
    if template_max_row > last:
        ws.delete_rows(last + 1, template_max_row - last)
    ws.auto_filter.ref = f"A2:{openpyxl.utils.get_column_letter(ncol)}{last}"
    # the template's conditional format is kept as is (openpyxl preserves it)
    if data_sheet_name:
        ws.title = data_sheet_name
    c = wb[COVER_SHEET]
    for addr, k in (('I3', 'company_doc_no'), ('J4', 'sheet'), ('L4', 'rev'),
                    ('I6', 'contractor_doc_no'), ('I8', 'rejlers_doc_no')):
        if cover.get(k):
            c[addr] = cover[k]
    return wb


def save_with_header(wb, cover, data_sheet_name):
    """Save the workbook and rewrite the data sheet's page header with the
    project's document numbers (the template hard-codes stale values, and
    openpyxl would write the line breaks as _x000a_). Returns .xlsx bytes."""
    ws = wb[COVER_SHEET]
    out = io.BytesIO()
    wb.save(out)
    g4 = (ws['G4'].value or '').strip()
    left = '\n'.join([f"COMPANY DOCUMENT NUMBER: {cover.get('company_doc_no', '')}",
                      f"CONTRACTOR DOCUMENT NUMBER: {cover.get('contractor_doc_no', '')}",
                      f"REJLERS DOCUMENT NUMBER: {cover.get('rejlers_doc_no', '')}"])
    right = f"{cover.get('company_doc_no', '')}_Rev.{cover.get('rev', '')}"
    return _set_header(out.getvalue(), data_sheet_name, left,
                       re.sub(r'[ \t]+\n', '\n', re.sub(r'[ \t]{2,}', ' ', g4)).strip(),
                       right)
