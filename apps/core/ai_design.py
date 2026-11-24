"""RADAI Intelligence design system: Rejlers-branded Word, PDF and Excel output for generated documents.

Chat text uses light Markdown (headings, bullet and numbered lists, **bold**, pipe tables). The
builders turn that into real document structure under a Rejlers header and a footer that marks the
file as an AI-generated draft. `plain` keeps the old unstyled output.
"""
import io
import re
from datetime import date
from pathlib import Path

from django.conf import settings

BRAND = {
    'navy': '0B2E59',       # Rejlers navy: headings, header rule, table header
    'accent': '4C30EF',     # RADAI Intelligence violet: small accents
    'ink': '1F2937',
    'muted': '6B7280',
    'zebra': 'F3F6FA',
    'font': 'Calibri',
}
DISCLAIMER = 'AI-generated draft from RADAI Intelligence. Not engineering-approved; verify before use.'
DESIGNS = ('rejlers', 'plain')


def logo_path():
    path = Path(settings.BASE_DIR) / 'apps' / 'procurement' / 'assets' / 'rejlers-pr-po-logo.png'
    return path if path.exists() else None


# ---------------------------------------------------------------- light Markdown parsing
_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def parse_blocks(text):
    """Return a list of (kind, value): heading (level, text), bullet, number, table (rows), para."""
    blocks, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        heading = re.match(r'^(#{1,4})\s+(.*)$', stripped)
        if heading:
            blocks.append(('heading', (len(heading.group(1)), heading.group(2).strip())))
        elif stripped.startswith('|') and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            rows = [[cell.strip() for cell in stripped.strip('|').split('|')]]
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append([cell.strip() for cell in lines[i].strip().strip('|').split('|')])
                i += 1
            blocks.append(('table', rows))
            continue
        elif re.match(r'^[-*•]\s+', stripped):
            blocks.append(('bullet', re.sub(r'^[-*•]\s+', '', stripped)))
        elif re.match(r'^\d+[.)]\s+', stripped):
            blocks.append(('number', re.sub(r'^\d+[.)]\s+', '', stripped)))
        else:
            blocks.append(('para', stripped))
        i += 1
    return blocks


def _runs(text):
    """Split **bold** segments: [(text, is_bold)]."""
    parts = re.split(r'(\*\*[^*]+\*\*)', text)
    return [(p[2:-2], True) if p.startswith('**') and p.endswith('**') else (p, False) for p in parts if p]


def _title(filename):
    title = Path(filename).stem.replace('_', ' ').replace('-', ' ').strip() or 'RADAI document'
    return title[0].upper() + title[1:]


# ---------------------------------------------------------------- Word
def build_docx(filename, content, author=''):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    navy, muted = RGBColor.from_string(BRAND['navy']), RGBColor.from_string(BRAND['muted'])
    doc = Document()
    section = doc.sections[0]
    section.left_margin = section.right_margin = Cm(2)
    section.top_margin, section.bottom_margin = Cm(2.4), Cm(2)
    base = doc.styles['Normal']
    base.font.name, base.font.size = BRAND['font'], Pt(10.5)
    base.font.color.rgb = RGBColor.from_string(BRAND['ink'])
    for level, size in ((1, 16), (2, 13), (3, 11.5), (4, 11)):
        style = doc.styles[f'Heading {level}']
        style.font.name, style.font.size, style.font.color.rgb, style.font.bold = BRAND['font'], Pt(size), navy, True

    # Header: logo left, product and title right, navy rule under it.
    header = section.header
    table = header.add_table(rows=1, cols=2, width=section.page_width - section.left_margin - section.right_margin)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right = table.rows[0].cells
    logo = logo_path()
    if logo:
        left.paragraphs[0].add_run().add_picture(str(logo), height=Cm(0.9))
    else:
        run = left.paragraphs[0].add_run('REJLERS')
        run.bold, run.font.color.rgb = True, navy
    para = right.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = para.add_run('RADAI Intelligence\n')
    run.font.size, run.font.color.rgb, run.bold = Pt(8), muted, True
    run = para.add_run(_title(filename))
    run.font.size, run.font.color.rgb = Pt(9), navy
    rule = header.add_paragraph()
    border = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    for key, value in (('w:val', 'single'), ('w:sz', '8'), ('w:space', '1'), ('w:color', BRAND['navy'])):
        bottom.set(qn(key), value)
    border.append(bottom)
    rule._p.get_or_add_pPr().append(border)

    # Footer: disclaimer, date and page number.
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run(f'{DISCLAIMER}  ·  {date.today():%d %b %Y}  ·  Page ')
    run.font.size, run.font.color.rgb = Pt(7.5), muted
    for kind, text in (('begin', None), (None, 'PAGE'), ('end', None)):
        r = footer.add_run()
        r.font.size, r.font.color.rgb = Pt(7.5), muted
        if kind:
            fld = OxmlElement('w:fldChar')
            fld.set(qn('w:fldCharType'), kind)
            r._r.append(fld)
        else:
            instr = OxmlElement('w:instrText')
            instr.set(qn('xml:space'), 'preserve')
            instr.text = text
            r._r.append(instr)

    # Title block.
    doc.add_heading(_title(filename), level=1)
    meta = doc.add_paragraph()
    run = meta.add_run(f'Prepared with RADAI Intelligence{f" by {author}" if author else ""} · {date.today():%d %B %Y}')
    run.font.size, run.font.color.rgb, run.italic = Pt(9), muted, True

    def add_runs(paragraph, text):
        for piece, bold in _runs(text):
            paragraph.add_run(piece).bold = bold

    for kind, value in parse_blocks(content):
        if kind == 'heading':
            level, text = value
            doc.add_heading(text, level=min(4, level + 1) if level == 1 else min(4, level))
        elif kind == 'bullet':
            add_runs(doc.add_paragraph(style='List Bullet'), value)
        elif kind == 'number':
            add_runs(doc.add_paragraph(style='List Number'), value)
        elif kind == 'table':
            width = max(len(r) for r in value)
            grid = doc.add_table(rows=len(value), cols=width)
            grid.style = 'Table Grid'
            for r_index, row in enumerate(value):
                for c_index in range(width):
                    cell = grid.cell(r_index, c_index)
                    cell.text = ''
                    add_runs(cell.paragraphs[0], row[c_index] if c_index < len(row) else '')
                    for run in cell.paragraphs[0].runs:
                        run.font.size = Pt(9.5)
                        if r_index == 0:
                            run.bold, run.font.color.rgb = True, RGBColor(0xFF, 0xFF, 0xFF)
                    shade = BRAND['navy'] if r_index == 0 else (BRAND['zebra'] if r_index % 2 == 0 else None)
                    if shade:
                        fill = OxmlElement('w:shd')
                        fill.set(qn('w:val'), 'clear')
                        fill.set(qn('w:fill'), shade)
                        cell._tc.get_or_add_tcPr().append(fill)
            doc.add_paragraph()
        else:
            add_runs(doc.add_paragraph(), value)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF
def build_pdf(filename, content, author=''):
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    navy, muted = colors.HexColor('#' + BRAND['navy']), colors.HexColor('#' + BRAND['muted'])
    title = _title(filename)
    sheet = getSampleStyleSheet()
    body = ParagraphStyle('Body', parent=sheet['BodyText'], fontName='Helvetica', fontSize=10, leading=14.5,
                          textColor=colors.HexColor('#' + BRAND['ink']), spaceAfter=5)
    heads = {level: ParagraphStyle(f'H{level}', parent=sheet['Heading1'], fontName='Helvetica-Bold',
                                   fontSize=size, leading=size + 4, textColor=navy, spaceBefore=10, spaceAfter=5)
             for level, size in ((1, 18), (2, 14), (3, 12), (4, 11))}
    meta = ParagraphStyle('Meta', parent=body, fontName='Helvetica-Oblique', fontSize=8.5, textColor=muted, spaceAfter=12)
    cell = ParagraphStyle('Cell', parent=body, fontSize=8.5, leading=11, spaceAfter=0)
    head_cell = ParagraphStyle('HeadCell', parent=cell, fontName='Helvetica-Bold', textColor=colors.white)

    def rich(text):
        return ''.join(f'<b>{escape(p)}</b>' if bold else escape(p) for p, bold in _runs(text))

    logo = logo_path()

    def chrome(canvas, document):
        width, height = A4
        canvas.saveState()
        if logo:
            canvas.drawImage(str(logo), 2 * cm, height - 1.75 * cm, height=0.85 * cm, width=3.2 * cm,
                             preserveAspectRatio=True, mask='auto', anchor='w')
        canvas.setFont('Helvetica-Bold', 7.5)
        canvas.setFillColor(muted)
        canvas.drawRightString(width - 2 * cm, height - 1.25 * cm, 'RADAI INTELLIGENCE')
        canvas.setFont('Helvetica', 8.5)
        canvas.setFillColor(navy)
        canvas.drawRightString(width - 2 * cm, height - 1.65 * cm, title[:90])
        canvas.setStrokeColor(navy)
        canvas.setLineWidth(1.2)
        canvas.line(2 * cm, height - 2.0 * cm, width - 2 * cm, height - 2.0 * cm)
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(muted)
        canvas.drawString(2 * cm, 1.2 * cm, DISCLAIMER)
        canvas.drawRightString(width - 2 * cm, 1.2 * cm, f'{date.today():%d %b %Y}  ·  Page {document.page}')
        canvas.restoreState()

    story = [Paragraph(escape(title), heads[1]),
             Paragraph(escape(f'Prepared with RADAI Intelligence{f" by {author}" if author else ""} · {date.today():%d %B %Y}'), meta)]
    pending = []

    def flush(kind):
        if pending:
            story.append(ListFlowable([ListItem(Paragraph(rich(t), body), leftIndent=12) for t in pending],
                                      bulletType='1' if kind == 'number' else 'bullet', start='1' if kind == 'number' else None,
                                      bulletFontSize=8, leftIndent=14))
            pending.clear()

    previous = None
    for kind, value in parse_blocks(content):
        if kind in ('bullet', 'number'):
            if previous not in (kind, None) and previous in ('bullet', 'number'):
                flush(previous)
            pending.append(value)
            previous = kind
            continue
        flush(previous)
        previous = kind
        if kind == 'heading':
            level, text = value
            story.append(Paragraph(rich(text), heads[min(4, level + 1)]))
        elif kind == 'table':
            width = max(len(r) for r in value)
            rows = [[Paragraph(rich(r[c] if c < len(r) else ''), head_cell if i == 0 else cell) for c in range(width)]
                    for i, r in enumerate(value)]
            grid = Table(rows, repeatRows=1, hAlign='LEFT', colWidths=[(A4[0] - 4 * cm) / width] * width)
            grid.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), navy),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#' + BRAND['zebra'])]),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#D5DCE6')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.extend([grid, Spacer(1, 8)])
        else:
            story.append(Paragraph(rich(value), body))
    flush(previous)

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2.6 * cm, bottomMargin=2 * cm,
                      title=title, author='RADAI Intelligence').build(story, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()


# ---------------------------------------------------------------- Excel
def build_xlsx(filename, content, author=''):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = (_title(filename)[:31] or 'Sheet1').replace('/', '-')
    tables = [value for kind, value in parse_blocks(content) if kind == 'table']
    rows = tables[0] if tables else [['Content']] + [[line] for line in content.splitlines() if line.strip()]
    ws.append([f'{_title(filename)} — RADAI Intelligence ({date.today():%d %b %Y})'])
    ws['A1'].font = Font(bold=True, size=13, color=BRAND['navy'])
    ws.append([DISCLAIMER])
    ws['A2'].font = Font(italic=True, size=8, color=BRAND['muted'])
    ws.append([])
    for index, row in enumerate(rows):
        ws.append([re.sub(r'\*\*([^*]+)\*\*', r'\1', cell) for cell in row])
        if index == 0:
            for c in ws[ws.max_row]:
                c.font = Font(bold=True, color='FFFFFF')
                c.fill = PatternFill('solid', fgColor=BRAND['navy'])
                c.alignment = Alignment(vertical='center')
    ws.freeze_panes = 'A5'
    for column in ws.iter_cols(min_row=4):
        letter = column[0].column_letter
        ws.column_dimensions[letter].width = min(60, max(12, max(len(str(c.value or '')) for c in column) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
