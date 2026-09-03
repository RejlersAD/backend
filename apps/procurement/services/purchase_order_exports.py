"""Purchase Order PDF/DOCX exports with ordered supporting attachments."""

from __future__ import annotations

import html
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.utils.html import strip_tags
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from openpyxl import load_workbook
from PIL import Image as PILImage
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

JARMO_NAME = 'Jarmo Suominen'
JARMO_TITLE = 'Sr. Vice President, Middle East\nCEO, Rejlers Abu Dhabi'
JARMO_COMPANY = 'Rejlers International Engineering Solutions AB'


def _value(value, fallback='—'):
    rendered = str(value or '').strip()
    return rendered or fallback


def _decode_html(value):
    """Decode editor content, including values that were escaped more than once."""
    decoded = str(value or '')
    for _ in range(3):
        next_value = html.unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded.replace('\xa0', ' ')


def _html_blocks(value):
    """Return readable text blocks from the browser rich-text editor markup."""
    source = _decode_html(value)
    if not source.strip():
        return []

    # Preserve editor paragraphs, lists, line breaks, and table rows before
    # removing unsupported markup. Django strip_tags does not decode entities.
    source = re.sub(r'(?is)<br\s*/?>', '\n', source)
    source = re.sub(r'(?is)<li\b[^>]*>', '\n• ', source)
    source = re.sub(r'(?is)</(?:p|div|h[1-6]|li|blockquote|pre|tr)>', '\n', source)
    source = re.sub(r'(?is)</(?:td|th)>', '\t', source)
    plain = _decode_html(strip_tags(source)).replace('\r\n', '\n').replace('\r', '\n')

    blocks = []
    for raw_line in plain.split('\n'):
        line = re.sub(r'[\t \f\v]+', ' ', raw_line).strip()
        if line:
            blocks.append(line)
    return blocks


def _pdf_rich_text(value, styles):
    flowables = []
    for block in _html_blocks(value):
        is_bullet = block.startswith('• ')
        content = block[2:].strip() if is_bullet else block
        style = styles['bullet'] if is_bullet else styles['body']
        flowables.extend((
            Paragraph(escape(content), style, bulletText='•' if is_bullet else None),
            Spacer(1, 1.5 * mm),
        ))
    return flowables


def _docx_rich_text(document, value):
    for block in _html_blocks(value):
        is_bullet = block.startswith('• ')
        content = block[2:].strip() if is_bullet else block
        document.add_paragraph(content, style='List Bullet' if is_bullet else None)


def _money(value, currency):
    return f'{currency or "AED"} {float(value or 0):,.2f}'


def _items(order):
    recorded = list(order.items or [])
    if not recorded:
        subtotal = max(
            0,
            float(order.total_amount or 0)
            - float(order.tax_amount or 0)
            + float(order.discount_amount or 0),
        )
        recorded = [{
            'description': order.title or order.description,
            'quantity': 1,
            'unit_price': subtotal,
            'uom': 'LOT',
        }]
    normalized = []
    for index, item in enumerate(recorded):
        quantity = float(item.get('quantity', item.get('qty', 1)) or 0)
        unit_price = float(item.get('unit_price', item.get('price', 0)) or 0)
        discount = float(item.get('discount', 0) or 0)
        total = float(item.get('total', item.get('line_total', 0)) or 0)
        if not total:
            total = max(0, (quantity * unit_price) - discount)
        normalized.append({
            'line': item.get('line_code') or item.get('item_code') or index + 1,
            'description': item.get('description') or item.get('item') or item.get('name') or order.title,
            'quantity': quantity,
            'uom': item.get('uom') or item.get('unit') or 'EA',
            'unit_price': unit_price,
            'total': total,
        })
    return normalized


def _attachment(entry, index):
    if isinstance(entry, str):
        return {
            'title': f'Attachment {index + 1}',
            'description': Path(entry).name,
            'filename': Path(entry).name,
            's3_url': entry,
        }
    entry = dict(entry or {})
    return {
        **entry,
        'title': _value(entry.get('title'), f'Attachment {index + 1}'),
        'description': _value(
            entry.get('description'),
            entry.get('filename') or entry.get('name') or f'Attachment {index + 1}',
        ),
        'filename': _value(entry.get('filename') or entry.get('name'), f'attachment-{index + 1}'),
    }


def _pdf_styles():
    styles = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('POTitle', parent=styles['Title'], fontSize=17, leading=21, textColor=colors.HexColor('#16689b')),
        'heading': ParagraphStyle('POHeading', parent=styles['Heading2'], fontSize=10, leading=13, spaceBefore=8, spaceAfter=5, textColor=colors.HexColor('#1f2937')),
        'body': ParagraphStyle('POBody', parent=styles['BodyText'], fontSize=8.5, leading=11),
        'bullet': ParagraphStyle('POBullet', parent=styles['BodyText'], fontSize=8.5, leading=11, leftIndent=5 * mm, firstLineIndent=-3 * mm),
        'small': ParagraphStyle('POSmall', parent=styles['BodyText'], fontSize=7, leading=9),
        'right': ParagraphStyle('PORight', parent=styles['BodyText'], fontSize=8.5, leading=11, alignment=TA_RIGHT),
        'cover': ParagraphStyle('POCover', parent=styles['Title'], fontSize=22, leading=28, alignment=TA_CENTER, textColor=colors.HexColor('#16689b')),
        'cover_body': ParagraphStyle('POCoverBody', parent=styles['BodyText'], fontSize=12, leading=18, alignment=TA_CENTER),
    }


def _pdf_page(canvas, document, order):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(colors.HexColor('#16689b'))
    canvas.setFont('Helvetica-Bold', 7)
    canvas.drawString(16 * mm, height - 10 * mm, _value(order.po_number, 'PO NUMBER PENDING'))
    canvas.setStrokeColor(colors.HexColor('#9ca3af'))
    canvas.line(16 * mm, 11 * mm, width - 16 * mm, 11 * mm)
    canvas.setFillColor(colors.HexColor('#64748b'))
    canvas.setFont('Helvetica', 6.5)
    canvas.drawString(16 * mm, 7 * mm, 'Rejlers International Engineering Solutions AB')
    canvas.drawRightString(width - 16 * mm, 7 * mm, f'Page {document.page}')
    canvas.restoreState()


def _main_pdf(order):
    output = BytesIO()
    styles = _pdf_styles()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=_value(order.po_number),
    )
    currency = order.currency or 'AED'
    items = _items(order)
    subtotal = sum(item['total'] for item in items)
    tax = float(order.tax_amount or 0)
    total = float(order.total_amount or subtotal + tax)
    vendor = getattr(order, 'vendor', None)
    story = [
        Paragraph('PURCHASE ORDER', styles['title']),
        Paragraph(escape(_value(order.po_number, 'PO NUMBER PENDING')), styles['heading']),
        Spacer(1, 5 * mm),
        Table([
            [Paragraph('<b>Seller information</b>', styles['body']), Paragraph(escape(_value(getattr(vendor, 'name', None))), styles['body'])],
            [Paragraph('<b>Seller Reference</b>', styles['body']), Paragraph(escape(_value(order.seller_reference)), styles['body'])],
            [Paragraph('<b>Quote Ref.</b>', styles['body']), Paragraph(escape(_value(order.quote_ref)), styles['body'])],
            [Paragraph('<b>Project</b>', styles['body']), Paragraph(escape(_value(order.project_number or order.rad_project_no)), styles['body'])],
            [Paragraph('<b>Payment Terms</b>', styles['body']), Paragraph(escape(_value(order.payment_terms)), styles['body'])],
            [Paragraph('<b>Delivery Terms</b>', styles['body']), Paragraph(escape(_value(order.delivery_terms)), styles['body'])],
        ], colWidths=[45 * mm, 125 * mm], style=TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#64748b')),
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ])),
        Spacer(1, 5 * mm),
        Paragraph('<b>Purchase Summary</b>', styles['heading']),
        Paragraph(escape(_value(getattr(order, 'summary', None) or order.title)), styles['body']),
        Spacer(1, 8 * mm),
        Paragraph('<b>Approved by:</b>', styles['heading']),
        Paragraph(escape(_value(order.approved_by_name, JARMO_NAME)), styles['body']),
        Paragraph(escape(_value(order.approved_by_title, JARMO_TITLE)).replace('\n', '<br/>'), styles['body']),
        Paragraph(JARMO_COMPANY, styles['body']),
        Paragraph(f'<b>Date:</b> {escape(_value(order.approved_date, ""))}', styles['body']),
        PageBreak(),
        Paragraph('PO DESCRIPTION &amp; SCOPE', styles['heading']),
    ]
    narrative = _pdf_rich_text(order.description, styles)
    story.extend(narrative or [Paragraph(escape(_value(order.title)), styles['body'])])
    # Match the live A4 document: the price summary starts on a clean page.
    # This also prevents an orphaned heading or split table after long scope text.
    story.extend((PageBreak(), Paragraph('SUMMARY OF PRICES', styles['heading'])))
    price_rows = [[
        Paragraph('<b>Line</b>', styles['small']),
        Paragraph('<b>Description</b>', styles['small']),
        Paragraph('<b>Qty.</b>', styles['small']),
        Paragraph('<b>UOM</b>', styles['small']),
        Paragraph('<b>Rate</b>', styles['small']),
        Paragraph('<b>Total Price</b>', styles['small']),
    ]]
    for item in items:
        price_rows.append([
            Paragraph(escape(str(item['line'])), styles['small']),
            Paragraph(escape(_value(item['description'])), styles['small']),
            Paragraph(f'{item["quantity"]:g}', styles['right']),
            Paragraph(escape(_value(item['uom'], '')), styles['small']),
            Paragraph(escape(_money(item['unit_price'], currency)), styles['right']),
            Paragraph(escape(_money(item['total'], currency)), styles['right']),
        ])
    story.extend([
        Table(price_rows, repeatRows=1, colWidths=[12 * mm, 76 * mm, 14 * mm, 15 * mm, 26 * mm, 31 * mm], style=TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#64748b')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ])),
        Spacer(1, 6 * mm),
        Table([
            ['Total Price:', _money(subtotal, currency)],
            [f'VAT ({float(order.vat_percentage or 0):g}%):', _money(tax, currency)],
            ['Total Sum:', _money(total, currency)],
        ], colWidths=[55 * mm, 42 * mm], hAlign='RIGHT', style=TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#475569')),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
        ])),
    ])
    document.build(
        story,
        onFirstPage=lambda canvas, doc: _pdf_page(canvas, doc, order),
        onLaterPages=lambda canvas, doc: _pdf_page(canvas, doc, order),
    )
    return output.getvalue()


def _cover_pdf(order, attachment, index):
    output = BytesIO()
    styles = _pdf_styles()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=25 * mm, rightMargin=25 * mm, topMargin=35 * mm, bottomMargin=25 * mm)
    document.build([
        Spacer(1, 55 * mm),
        Paragraph(f'ATTACHMENT - {index + 1}', styles['cover']),
        Spacer(1, 12 * mm),
        Paragraph(escape(_value(attachment['description'])), styles['cover_body']),
    ], onFirstPage=lambda canvas, doc: _pdf_page(canvas, doc, order))
    return output.getvalue()


def _download_attachment(attachment):
    from django.conf import settings
    from django.core.files.storage import default_storage

    key = str(attachment.get('s3_key') or '').strip()
    if not key:
        return None
    try:
        with default_storage.open(key, 'rb') as stored_file:
            return stored_file.read()
    except Exception:
        # Older PO uploads used a raw bucket key without the MediaStorage
        # prefix. Keep those files exportable after adopting default_storage.
        if not getattr(settings, 'USE_S3', False):
            return None
        try:
            from apps.core.s3_utils import S3Client

            output = BytesIO()
            if S3Client().download_file(key, output):
                return output.getvalue()
        except Exception:
            return None
    return None


def _flowables_pdf(flowables):
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm).build(flowables)
    return output.getvalue()


def _image_pdf(content):
    source = BytesIO(content)
    with PILImage.open(source) as image:
        width, height = image.size
    max_width, max_height = 180 * mm, 267 * mm
    scale = min(max_width / width, max_height / height)
    source.seek(0)
    rendered = Image(source, width=width * scale, height=height * scale)
    rendered.hAlign = 'CENTER'
    return _flowables_pdf([rendered])


def _docx_pdf(content):
    document = Document(BytesIO(content))
    styles = _pdf_styles()
    flowables = []
    for paragraph in document.paragraphs:
        value = paragraph.text.strip()
        if value:
            flowables.extend([Paragraph(escape(value), styles['body']), Spacer(1, 2 * mm)])
    for table in document.tables:
        rows = [[Paragraph(escape(cell.text), styles['small']) for cell in row.cells] for row in table.rows]
        if rows:
            flowables.extend([Table(rows, repeatRows=1, style=TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.35, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ])), Spacer(1, 3 * mm)])
    return _flowables_pdf(flowables or [Paragraph('The attached Word document has no renderable text.', styles['body'])])


def _xlsx_pdf(content):
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    styles = _pdf_styles()
    flowables = []
    for sheet in workbook.worksheets:
        flowables.append(Paragraph(escape(sheet.title), styles['heading']))
        rows = []
        for row in sheet.iter_rows(values_only=True):
            values = [Paragraph(escape(_value(cell, '')), styles['small']) for cell in row]
            if any(str(cell or '').strip() for cell in row):
                rows.append(values)
            if len(rows) >= 100:
                break
        if rows:
            available = 180 * mm
            columns = max(len(row) for row in rows)
            rows = [row + [Paragraph('', styles['small'])] * (columns - len(row)) for row in rows]
            flowables.extend([Table(rows, repeatRows=1, colWidths=[available / columns] * columns, style=TableStyle([
                ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ])), PageBreak()])
    if flowables and isinstance(flowables[-1], PageBreak):
        flowables.pop()
    return _flowables_pdf(flowables or [Paragraph('The attached workbook has no renderable cells.', styles['body'])])


def _attachment_pdf(content, filename, content_type=''):
    suffix = Path(filename).suffix.lower()
    if suffix == '.pdf' or content_type == 'application/pdf':
        PdfReader(BytesIO(content))
        return content
    if suffix in {'.png', '.jpg', '.jpeg'} or str(content_type).startswith('image/'):
        return _image_pdf(content)
    if suffix == '.docx':
        return _docx_pdf(content)
    if suffix == '.xlsx':
        return _xlsx_pdf(content)
    return None


def build_purchase_order_pdf(order):
    """Return one PDF containing the PO, attachment covers, and renderable files."""
    writer = PdfWriter()
    warnings = []

    def append(pdf_bytes):
        for page in PdfReader(BytesIO(pdf_bytes)).pages:
            writer.add_page(page)

    append(_main_pdf(order))
    for index, raw_attachment in enumerate(order.attachments or []):
        attachment = _attachment(raw_attachment, index)
        append(_cover_pdf(order, attachment, index))
        content = _download_attachment(attachment)
        if content is None:
            warnings.append(f'{attachment["filename"]}: file could not be downloaded')
            continue
        try:
            rendered = _attachment_pdf(content, attachment['filename'], attachment.get('content_type'))
            if rendered:
                append(rendered)
            else:
                warnings.append(f'{attachment["filename"]}: format cannot be rendered in PDF')
        except Exception as exc:  # The PO must remain downloadable if one supporting file is corrupt.
            warnings.append(f'{attachment["filename"]}: {type(exc).__name__}')

    output = BytesIO()
    writer.write(output)
    return output.getvalue(), warnings


def build_purchase_order_docx(order):
    """Return the editable PO through Summary of Prices; attachments are excluded."""
    document = Document()
    section = document.sections[0]
    section.page_width = Inches(8.27)
    section.page_height = Inches(11.69)
    section.top_margin = Inches(0.6)
    section.bottom_margin = Inches(0.6)
    title = document.add_heading('PURCHASE ORDER', level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph(_value(order.po_number, 'PO NUMBER PENDING')).alignment = WD_ALIGN_PARAGRAPH.CENTER

    vendor = getattr(order, 'vendor', None)
    details = document.add_table(rows=0, cols=2)
    details.style = 'Table Grid'
    for label, value in (
        ('Seller information', getattr(vendor, 'name', None)),
        ('Seller Reference', order.seller_reference),
        ('Quote Ref.', order.quote_ref),
        ('Project', order.project_number or order.rad_project_no),
        ('Payment Terms', order.payment_terms),
        ('Delivery Terms', order.delivery_terms),
    ):
        cells = details.add_row().cells
        cells[0].text = label
        cells[1].text = _value(value)

    document.add_heading('Purchase Summary', level=1)
    document.add_paragraph(_value(getattr(order, 'summary', None) or order.title))
    document.add_heading('Approved by', level=1)
    document.add_paragraph(_value(order.approved_by_name, JARMO_NAME))
    document.add_paragraph(_value(order.approved_by_title, JARMO_TITLE))
    document.add_paragraph(JARMO_COMPANY)
    document.add_page_break()
    document.add_heading('PO Description & Scope', level=1)
    if _html_blocks(order.description):
        _docx_rich_text(document, order.description)
    else:
        document.add_paragraph(_value(order.title))
    document.add_page_break()
    document.add_heading('Summary of Prices', level=1)
    items = _items(order)
    table = document.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    for cell, heading in zip(table.rows[0].cells, ('Line', 'Description', 'Qty.', 'UOM', 'Rate', 'Total Price')):
        cell.text = heading
    currency = order.currency or 'AED'
    for item in items:
        cells = table.add_row().cells
        values = (
            item['line'], item['description'], f'{item["quantity"]:g}', item['uom'],
            _money(item['unit_price'], currency), _money(item['total'], currency),
        )
        for cell, value in zip(cells, values):
            cell.text = str(value)
    subtotal = sum(item['total'] for item in items)
    tax = float(order.tax_amount or 0)
    total = float(order.total_amount or subtotal + tax)
    totals = document.add_paragraph()
    totals.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    totals.add_run(
        f'Total Price: {_money(subtotal, currency)}\n'
        f'VAT ({float(order.vat_percentage or 0):g}%): {_money(tax, currency)}\n'
        f'Total Sum: {_money(total, currency)}'
    ).bold = True
    normal_style = document.styles['Normal']
    normal_style.font.name = 'Arial'
    normal_style.font.size = Pt(9)
    output = BytesIO()
    document.save(output)
    return output.getvalue()
