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
COMPANY_NAME = 'Rejlers International Engineering Solutions'
COMPANY_ADDRESS = (
    'Rejlers Tower, 13th floor, AI Hamdan Street, P.O. Box 39317, '
    'Abu Dhabi, United Arab Emirates'
)
COMPANY_PHONE = '+971 50 560 6987'
COMPANY_WEBSITE = 'www.rejlers.ae'
BRAND_BLUE = colors.HexColor('#0870aa')
BRAND_TEXT_BLUE = colors.HexColor('#3275b6')
BRAND_NAVY = colors.HexColor('#1f2d55')


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


def _date_text(value):
    if not value:
        return '—'
    if hasattr(value, 'strftime'):
        return value.strftime('%d %b %Y')
    try:
        from datetime import date

        return date.fromisoformat(str(value)[:10]).strftime('%d %b %Y')
    except (TypeError, ValueError):
        return str(value)


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


def _draw_rejlers_wordmark(canvas, x, y, width, color):
    """Draw a compact vector wordmark without relying on frontend assets."""
    scale = width / 42.0
    canvas.saveState()
    canvas.setStrokeColor(color)
    canvas.setLineWidth(max(0.65, 1.15 * scale))
    canvas.setLineCap(1)
    canvas.setLineJoin(1)
    canvas.line(x, y + 1.0 * scale, x + 4.2 * scale, y + 5.2 * scale)
    canvas.line(x + 4.2 * scale, y + 5.2 * scale, x + 4.2 * scale, y + 1.0 * scale)
    canvas.setFillColor(color)
    canvas.setFont('Helvetica', max(4.5, 5.8 * scale))
    canvas.drawString(x + 6.2 * scale, y, 'REJLERS')
    canvas.restoreState()


def _pdf_page(canvas, document, order, page_number=None):
    """Draw the same branded header/footer used by Print Preview."""
    canvas.saveState()
    width, height = A4

    # Header: document identity on the left, Rejlers wordmark on the right.
    left = 16 * mm
    top = height - 11 * mm
    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica-Bold', 8.5)
    canvas.drawString(left, top, 'PURCHASE ORDER')
    canvas.setFont('Helvetica-Bold', 7.5)
    canvas.drawString(left, top - 4 * mm, _value(order.po_number, 'PO NUMBER PENDING'))
    canvas.setFillColor(colors.HexColor('#64748b'))
    canvas.setFont('Helvetica', 5.5)
    canvas.drawString(left, top - 7 * mm, _value(getattr(order, 'form_note', None), '(PO no. to be used in all documents)'))
    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica-Bold', 7)
    canvas.drawString(left, top - 12 * mm, _date_text(getattr(order, 'po_date', None)))

    logo_width = 37 * mm
    logo_x = width - left - logo_width
    _draw_rejlers_wordmark(canvas, logo_x, top - 4 * mm, logo_width, BRAND_NAVY)
    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica-Bold', 5.7)
    canvas.drawRightString(width - left, top - 9 * mm, 'HOME OF THE')
    canvas.drawRightString(width - left, top - 12 * mm, 'LEARNING MINDS')

    # Footer: repeated white brand marks in the blue band, then the same
    # company/contact block and page number shown by the browser preview.
    band_x = left
    band_y = 12 * mm
    band_width = width - (2 * left)
    band_height = 7 * mm
    canvas.setFillColor(BRAND_BLUE)
    canvas.rect(band_x, band_y, band_width, band_height, fill=1, stroke=0)
    group_width = band_width / 5
    for index in (0, 2, 4):
        _draw_rejlers_wordmark(
            canvas,
            band_x + (index * group_width) + 2.5 * mm,
            band_y + 2.1 * mm,
            group_width - 5 * mm,
            colors.white,
        )
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 4.2)
    for index in (1, 3):
        center = band_x + ((index + 0.5) * group_width)
        canvas.drawCentredString(center, band_y + 4.2 * mm, 'HOME OF THE')
        canvas.drawCentredString(center, band_y + 2.2 * mm, 'LEARNING MINDS')

    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica', 4.6)
    canvas.drawString(left + 8 * mm, 9.2 * mm, COMPANY_NAME)
    canvas.drawString(left + 8 * mm, 7.0 * mm, COMPANY_ADDRESS)
    canvas.drawString(left + 8 * mm, 4.8 * mm, f'Tel: {COMPANY_PHONE} | {COMPANY_WEBSITE}')
    canvas.setFont('Helvetica', 5.2)
    canvas.drawRightString(width - left, 5.5 * mm, f'Page {page_number or document.page}')
    canvas.restoreState()


def _main_pdf(order):
    output = BytesIO()
    styles = _pdf_styles()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=34 * mm,
        bottomMargin=25 * mm,
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


def _cover_pdf(order, attachment, index, page_number):
    output = BytesIO()
    styles = _pdf_styles()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=25 * mm, rightMargin=25 * mm, topMargin=35 * mm, bottomMargin=25 * mm)
    document.build([
        Spacer(1, 55 * mm),
        Paragraph(f'ATTACHMENT - {index + 1}', styles['cover']),
        Spacer(1, 12 * mm),
        Paragraph(escape(_value(attachment['description'])), styles['cover_body']),
    ], onFirstPage=lambda canvas, doc: _pdf_page(canvas, doc, order, page_number))
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
        append(_cover_pdf(order, attachment, index, len(writer.pages) + 1))
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
