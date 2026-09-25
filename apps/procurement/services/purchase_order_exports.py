"""Purchase Order PDF/DOCX exports with ordered supporting attachments."""

from __future__ import annotations

import html
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.utils.html import strip_tags
from django.utils import timezone
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
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
    KeepInFrame,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.utils import ImageReader

from .approval_integrity import purchase_order_signature_issue
from .purchase_order_project_display import purchase_order_project_reference
from .purchase_order_introduction import BUYER_NAME as COMPANY_NAME, INTRODUCTION_KEY, purchase_order_introduction
from .purchase_order_document_options import show_scope_heading
from .purchase_order_word_pages import append_pdf_pages
from .purchase_order_approval_artwork import (
    approval_image_stream as _signature_stream,
    approval_stamp_stream,
    completed_jarmo_profile_artwork,
)
from .purchase_order_source_artwork import source_approval_artwork
from .purchase_order_sources import is_safe_source_storage_key, uploaded_purchase_order_sources
from .po_rich_content import append_docx_rich_content, parse_meaningful_rich_content, pdf_rich_flowables

JARMO_NAME = 'Jarmo Suominen'
JARMO_TITLE = 'Sr. Vice President, Middle East\nCEO, Rejlers Abu Dhabi'
JARMO_COMPANY = 'Rejlers International Engineering Solutions AB'
COMPANY_ADDRESS = (
    'Rejlers Tower, 13th floor, AI Hamdan Street, P.O. Box 39317, '
    'Abu Dhabi, United Arab Emirates'
)
COMPANY_PHONE = '+971 50 560 6987'
COMPANY_WEBSITE = 'www.rejlers.ae'
BRAND_BLUE = colors.HexColor('#0870aa')
BRAND_TEXT_BLUE = colors.HexColor('#3275b6')
BRAND_NAVY = colors.HexColor('#1f2d55')
LOGO_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'rejlers-pr-po-logo.png'
WHITE_LOGO_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'rejlers-pr-po-logo-white.png'
DEFAULT_INVOICE_ADDRESS = (
    'Attn. Mr. Aneef Thadikkarantavida\n'
    'aneef.thadikkarantavida@rejlers.ae\n'
    'cc. uae.finance@rejlers.ae\n'
    'uae.procurement@rejlers.ae\n'
    'Rejlers International Engineering\n'
    'Solutions AB\n'
    'PO Box 39317\n'
    'Abu Dhabi, UAE.\n'
    'Tel: +971 2 639 7449\n'
    'Fax: +971 2 639 7448'
)


class _FlowTable(Table):
    """Expose measured size when ReportLab splits a table inside a cell.

    ReportLab 4.0's in-row splitter reads the standard Flowable ``height``
    attribute, while Table.wrap() only sets its private ``_height``. Retain
    the measured public dimensions so nested fields can continue onto pages.
    """

    def wrap(self, availWidth, availHeight):
        self.width, self.height = super().wrap(availWidth, availHeight)
        return self.width, self.height


def _value(value, fallback='—'):
    rendered = str(value or '').strip()
    return rendered or fallback


def _approval_artwork(order, display, signature_issue):
    """Completion keeps genuine signing artwork visible; it creates no approval."""
    if signature_issue or not display['recorded']:
        return None, None, None
    profile_signature, company_stamp = completed_jarmo_profile_artwork(order)
    if profile_signature is not None:
        return profile_signature, company_stamp, None
    signature = _signature_stream(getattr(order, 'approval_signature', ''))
    if signature is None:
        return None, None, source_approval_artwork(order)
    return signature, company_stamp or approval_stamp_stream(getattr(order, 'approval_stamp', '')), None


def _approval_image(stream, width, height):
    image = Image(stream)
    image._restrictSize(width * mm, height * mm)
    image.hAlign = 'LEFT'
    return image


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


def _scope_narrative_blocks(value):
    """Keep authored layout, but do not turn empty editor markup into a page."""
    return parse_meaningful_rich_content(value, default_font_size=12)


def _pdf_rich_text(blocks, styles, width=None):
    # SimpleDocTemplate's frame reserves 6pt on each side inside its margins.
    # Tables and tab fields must use the same line width as its paragraphs.
    width = A4[0] - 32 * mm - 12 if width is None else width
    return pdf_rich_flowables(blocks, width, styles['body'])


def _docx_rich_text(document, blocks):
    section = document.sections[-1]
    width = (section.page_width - section.left_margin - section.right_margin) / Pt(1)
    append_docx_rich_content(document, blocks, width=width)


def _money(value, currency):
    return f'{currency or "AED"} {float(value or 0):,.2f}'


def _purchase_summary(order):
    contacts = getattr(order, 'contact_persons', None) or {}
    if isinstance(contacts, dict) and 'purchase_summary' in contacts:
        return _value(contacts.get('purchase_summary'), order.title)
    return _value(getattr(order, 'summary', None), order.title)


def _date_text(value):
    if not value:
        return '—'
    if hasattr(value, 'strftime'):
        rendered = value.strftime('%d %b %Y')
    try:
        from datetime import date

        rendered = date.fromisoformat(str(value)[:10]).strftime('%d %b %Y')
    except (TypeError, ValueError):
        return str(value)
    return rendered.replace(' Sep ', ' Sept ')


def _paragraph(value, style, bold=False):
    content = escape(_value(value)).replace('\n', '<br/>')
    return Paragraph(f'<b>{content}</b>' if bold else content, style)


def _buyer_reference(order):
    contacts = getattr(order, 'contact_persons', None) or {}
    references = contacts.get('buyer_references') or []
    rendered = []
    for reference in references:
        if not reference or not reference.get('name'):
            continue
        name = escape(str(reference.get('name')).strip())
        designation = str(reference.get('designation') or '').strip()
        email = str(reference.get('email') or '').strip()
        lines = [name]
        if designation:
            lines.append(f'<b>{escape(designation)}</b>')
        if email:
            lines.append(f'<font size="9">{escape(email)}</font>')
        rendered.append('<br/>'.join(lines))
    if not rendered:
        name = escape(_value(getattr(order, 'buyer_reference_pm', None), 'Richa Hannah Thomas'))
        designation = escape(_value(getattr(order, 'buyer_reference_designation', None), 'Procurement Manager'))
        email = escape(str(getattr(order, 'buyer_reference_email', '') or '').strip())
        email_line = f'<font size="9">{email}</font>' if email else ''
        rendered.append('<br/>'.join(filter(None, (name, f'<b>{designation}</b>', email_line))))
    return '<br/>'.join(rendered)


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
            **item,
            'line': item.get('line_code') or item.get('item_code') or index + 1,
            'description': item.get('description') or item.get('item') or item.get('name') or order.title,
            'specification': item.get('specification') or '',
            'comment': item.get('comment') or item.get('comments') or item.get('remarks') or item.get('notes') or '',
            'quantity': quantity,
            'uom': item.get('uom') or item.get('unit') or 'EA',
            'unit_price': unit_price,
            'discount': discount,
            'total': total,
        })
    return normalized


def _item_columns(order):
    defaults = {
        'line_code': 'Line Code', 'description': 'Item Description',
        'specification': 'Specification', 'comment': 'Comments',
        'quantity': 'Qty.', 'uom': 'UOM', 'unit_price': 'Rate',
        'discount': 'Discount', 'total_price': 'Total Price',
    }
    headers = getattr(order, 'items_table_headers', None) or defaults
    if not isinstance(headers, dict):
        headers = defaults
    order_keys = headers.get('__column_order')
    if not isinstance(order_keys, list):
        order_keys = [key for key in headers if not key.startswith('__')]
    seen, columns = set(), []
    for key in order_keys:
        if not isinstance(key, str) or key.startswith('__') or key in seen:
            continue
        title = headers.get(key)
        if not isinstance(title, str) or not title.strip():
            continue
        seen.add(key)
        columns.append((key, title.strip()))
    return columns


def _item_value(item, key, currency):
    if key in {'unit_price', 'discount', 'total_price'}:
        return _money(item['total' if key == 'total_price' else key], currency)
    if key == 'quantity':
        return f'{item["quantity"]:g}'
    return str(item.get('line' if key == 'line_code' else key) or '')


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
        'title': ParagraphStyle('POTitle', parent=styles['Title'], fontSize=15, leading=18, textColor=colors.HexColor('#16689b')),
        'heading': ParagraphStyle('POHeading', parent=styles['Heading2'], fontSize=12, leading=15, spaceBefore=8, spaceAfter=5, textColor=colors.HexColor('#1f2937')),
        'body': ParagraphStyle('POBody', parent=styles['BodyText'], fontSize=10.5, leading=13.5),
        'bullet': ParagraphStyle('POBullet', parent=styles['BodyText'], fontSize=10.5, leading=13.5, leftIndent=5 * mm, firstLineIndent=-3 * mm),
        'small': ParagraphStyle('POSmall', parent=styles['BodyText'], fontSize=7, leading=9),
        'right': ParagraphStyle('PORight', parent=styles['BodyText'], fontSize=8.5, leading=11, alignment=TA_RIGHT),
        'cover': ParagraphStyle('POCover', parent=styles['Title'], fontSize=22, leading=28, alignment=TA_CENTER, textColor=colors.HexColor('#16689b')),
        'cover_body': ParagraphStyle('POCoverBody', parent=styles['BodyText'], fontSize=12, leading=18, alignment=TA_CENTER),
        'preview': ParagraphStyle('POPreview', parent=styles['BodyText'], fontSize=10.5, leading=13.5, textColor=colors.HexColor('#334155')),
        'preview_bold': ParagraphStyle('POPreviewBold', parent=styles['BodyText'], fontSize=10.5, leading=13.5, fontName='Helvetica-Bold', textColor=colors.HexColor('#334155')),
        'preview_heading': ParagraphStyle('POPreviewHeading', parent=styles['Heading2'], fontSize=12, leading=15, fontName='Helvetica-Bold', textColor=colors.HexColor('#1f2937')),
    }


def _draw_rejlers_wordmark(canvas, x, y, width, color):
    """Draw the official wordmark, with a vector fallback for damaged installs."""
    logo_path = WHITE_LOGO_PATH if color == colors.white else LOGO_PATH
    if logo_path.exists():
        canvas.drawImage(
            ImageReader(str(logo_path)), x, y, width=width, height=width / 6.64,
            preserveAspectRatio=True, mask='auto', anchor='sw',
        )
        return
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
    canvas.setFont('Helvetica-Bold', 15)
    canvas.drawString(left, top, 'PURCHASE ORDER')
    canvas.setFont('Helvetica-Bold', 10)
    canvas.drawString(left, top - 6 * mm, _value(order.po_number, 'PO NUMBER PENDING'))
    canvas.setFillColor(colors.HexColor('#64748b'))
    canvas.setFont('Helvetica', 7.5)
    canvas.drawString(left, top - 10 * mm, _value(getattr(order, 'form_note', None), '(PO no. to be used in all documents)'))
    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica-Bold', 10)
    canvas.drawString(left, top - 16 * mm, _date_text(getattr(order, 'po_date', None)))

    logo_width = 27 * mm
    logo_x = width - left - logo_width
    _draw_rejlers_wordmark(canvas, logo_x, top - 1.5 * mm, logo_width, BRAND_NAVY)
    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica-Bold', 8.5)
    canvas.drawRightString(width - left, top - 6 * mm, 'HOME OF THE')
    canvas.drawRightString(width - left, top - 10 * mm, 'LEARNING MINDS')

    # Footer: repeated white brand marks in the blue band, then the same
    # company/contact block and page number shown by the browser preview.
    band_x = left
    # Keep the footer in the same position as the approved legacy A4 form,
    # leaving room for the full company/address block beneath the blue band.
    band_y = 29 * mm
    band_width = width - (2 * left)
    band_height = 7 * mm
    canvas.setFillColor(BRAND_BLUE)
    canvas.rect(band_x, band_y, band_width, band_height, fill=1, stroke=0)
    group_width = band_width / 5
    for index in (0, 2, 4):
        footer_logo_width = 21 * mm
        _draw_rejlers_wordmark(
            canvas,
            band_x + ((index + 0.5) * group_width) - (footer_logo_width / 2),
            band_y + 1.9 * mm,
            footer_logo_width,
            colors.white,
        )
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 7)
    for index in (1, 3):
        center = band_x + ((index + 0.5) * group_width)
        canvas.drawCentredString(center, band_y + 4.8 * mm, 'HOME of the')
        canvas.drawCentredString(center, band_y + 1.8 * mm, 'LEARNING MINDS')

    canvas.setFillColor(BRAND_TEXT_BLUE)
    canvas.setFont('Helvetica', 7)
    canvas.drawString(left + 8 * mm, 25.8 * mm, COMPANY_NAME)
    canvas.drawString(left + 8 * mm, 22.8 * mm, 'Rejlers Tower, 13th floor, AI Hamdan Street, P.O. Box 39317,')
    canvas.drawString(left + 8 * mm, 19.8 * mm, 'Abu Dhabi, United Arab Emirates')
    canvas.drawString(left + 8 * mm, 16.8 * mm, f'Tel: {COMPANY_PHONE} | {COMPANY_WEBSITE}')
    canvas.drawRightString(width - left, 17.5 * mm, f'Page {page_number or document.page}')
    canvas.restoreState()


def _project_display(order):
    reference = purchase_order_project_reference(order)
    contacts = getattr(order, 'contact_persons', None)
    explicitly_selected = isinstance(contacts, dict) and 'project_selections' in contacts
    return reference or ('—' if explicitly_selected else 'Multiple Projects')


def _approval_display(order):
    name = str(getattr(order, 'approved_by_name', '') or '').strip()
    approved_date = getattr(order, 'approved_date', None)
    approved_at = getattr(order, 'approved_at', None)
    if not approved_date and approved_at:
        approved_date = (timezone.localtime(approved_at) if timezone.is_aware(approved_at) else approved_at).date()
    recorded = bool(name and approved_date)
    status_code = str(getattr(order, 'status', '') or '').strip().lower()
    # A draft alone does not establish an approval request. Only an assigned
    # internal PO route can be pending; linked PR/source evidence is history.
    assigned_stages = [
        row for row in (getattr(order, 'approval_log', None) or [])
        if isinstance(row, dict)
        and not row.get('external') and not row.get('evidence_document_id')
        and row.get('source') != 'signed_purchase_requisition_pdf'
        and any(str(row.get(key) or '').strip() for key in (
            'user_id', 'approver_id', 'user_email', 'approver_email', 'email',
        ))
    ]
    decisions = {str(row.get('status') or 'pending').strip().lower() for row in assigned_stages}
    pending = (
        status_code in {'draft', 'pending_approval', 'sent', 'acknowledged', 'in_progress', 'partially_received'}
        and bool(decisions & {'pending', 'in_review', 'under_review'})
        and not decisions & {'rejected', 'not_approved', 'declined'}
    )
    unassigned = status_code in {'draft', 'pending_approval'} and not assigned_stages
    status_display = getattr(order, 'get_status_display', None)
    status = status_display() if callable(status_display) else str(getattr(order, 'status', '') or 'Not recorded').replace('_', ' ').title()
    return {
        'recorded': recorded, 'status': status,
        'heading': ('Approved by:' if recorded else 'Approval pending:' if pending
                    else 'Approval not requested:' if unassigned else 'Approval record:'),
        'name': name if recorded else JARMO_NAME if pending or unassigned else 'Not recorded',
        'title': str(getattr(order, 'approved_by_title', '') or '') if recorded else JARMO_TITLE if pending or unassigned else '',
        'date': approved_date if recorded else None,
    }


def _main_pdf(order, *, measure_cover=False):
    output = BytesIO()
    styles = _pdf_styles()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=34 * mm,
        bottomMargin=42 * mm,
        title=_value(order.po_number),
    )
    currency = order.currency or 'AED'
    items = _items(order)
    subtotal = float(order.net_amount) if getattr(order, 'net_amount', None) is not None else sum(item['total'] for item in items)
    tax = float(order.tax_amount or 0)
    total = float(order.total_amount or subtotal + tax)
    vendor = getattr(order, 'vendor', None)
    introduction = escape(purchase_order_introduction(order)).replace('\n', '<br/>')
    contacts = getattr(order, 'contact_persons', None)
    if introduction and (not isinstance(contacts, dict) or not isinstance(contacts.get(INTRODUCTION_KEY), str)):
        introduction = (
            f'We, {COMPANY_NAME} (Buyer), issue this purchase order to '
            f'<b>{escape(_value(getattr(vendor, "name", None)))}</b> (Seller).'
        )
    # The signed commercial cover is one page. Use compact, readable cover
    # typography without changing the scope/price-summary pages that follow.
    preview = ParagraphStyle('POCoverField', parent=styles['preview'], fontSize=9.5, leading=11.5)
    preview_bold = ParagraphStyle('POCoverFieldBold', parent=preview, fontName='Helvetica-Bold')

    def pair_rows(rows):
        return _FlowTable(
            [[
                Paragraph(f'<b>{escape(label)}:</b>', preview),
                value if isinstance(value, Paragraph) else _paragraph(value, preview, strong),
            ] for label, value, strong in rows],
            colWidths=[30 * mm, 52 * mm],
            splitInRow=1,
            style=TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 1.5 * mm),
                ('TOPPADDING', (0, 0), (-1, -1), 1.5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]),
        )

    invoice_address = DEFAULT_INVOICE_ADDRESS
    if getattr(order, 'invoicing_attn', None) or getattr(order, 'invoicing_emails', None):
        invoice_emails = getattr(order, 'invoicing_emails', None) or []
        if not isinstance(invoice_emails, (list, tuple)):
            invoice_emails = [invoice_emails]
        invoice_address = '\n'.join(filter(None, (
            str(getattr(order, 'invoicing_attn', '') or '').strip(),
            *[str(email).strip() for email in invoice_emails if email],
            'Rejlers International Engineering Solutions AB',
            'PO Box 39317', 'Abu Dhabi, UAE.', 'Tel: +971 2 639 7449',
            f'Fax: {_value(getattr(order, "company_fax", None), "+971 2 639 7448")}',
        )))
    invoice_address_lines = []
    for line in invoice_address.splitlines():
        rendered_line = escape(line)
        if '@' in line:
            rendered_line = f'<font size="9">{rendered_line}</font>'
        invoice_address_lines.append(rendered_line)
    invoice_address_paragraph = Paragraph('<br/>'.join(invoice_address_lines), preview)

    details = Table([[pair_rows([
        ('Seller', getattr(vendor, 'name', None), False),
        ('Seller Address', getattr(order, 'seller_address', None) or getattr(vendor, 'address', None), False),
        ('Invoicing Address', invoice_address_paragraph, False),
    ]), '', pair_rows([
        ('Seller Reference', getattr(order, 'seller_reference', None), False),
        ('Quote Ref.', getattr(order, 'quote_ref', None), False),
        ('License No.', getattr(order, 'seller_license_no', None), False),
        ('Buyer Reference', Paragraph(_buyer_reference(order), preview), False),
    ])]], colWidths=[84 * mm, 8 * mm, 84 * mm], splitInRow=1, style=TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    commercial = Table([[pair_rows([
        ('Payment Terms', getattr(order, 'payment_terms', None), False),
        ('Payment Mode', getattr(order, 'payment_mode', None), False),
        ('Project', _project_display(order), True),
    ]), '', pair_rows([
        ('Delivery terms', getattr(order, 'delivery_terms', None), False),
        ('Delivery date', _date_text(getattr(order, 'expected_delivery', None)), False),
        ('Marking', getattr(order, 'marking', None) or order.po_number, True),
    ])]], colWidths=[84 * mm, 8 * mm, 84 * mm], splitInRow=1, style=TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    summary_table = Table([[
        Paragraph(f'<b>Purchase Summary:</b><br/><b>{escape(_value(_purchase_summary(order)))}</b>', preview),
        '',
        _FlowTable([
            [Paragraph('<b>Total Purchase Price:</b>', preview), Paragraph(escape(f'{subtotal:,.2f} {currency}'), styles['right'])],
            [Paragraph(f'<b>VAT ({float(getattr(order, "vat_percentage", 0) or 0):g}%):</b>', preview), Paragraph(escape(f'{tax:,.2f} {currency}'), styles['right'])],
            [Paragraph('<b>Total Sum:</b>', preview_bold), Paragraph(escape(f'{total:,.2f} {currency}'), styles['right'])],
        ], colWidths=[45 * mm, 34 * mm], style=TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ])),
    ]], colWidths=[91 * mm, 6 * mm, 79 * mm], splitInRow=1, style=TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 1.2, colors.HexColor('#475569')),
        ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.HexColor('#475569')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5 * mm), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5 * mm),
    ]))
    approval = _approval_display(order)
    approval_name, approval_title = approval['name'], approval['title']
    signature_issue = purchase_order_signature_issue(order)
    signature_stream, stamp_stream, source_stream = _approval_artwork(order, approval, signature_issue)
    heading = '' if source_stream else f'<br/><b>{approval["heading"]}</b>'
    approved = [Paragraph(f'<b>PO status:</b> {escape(approval["status"])}{heading}', preview)]
    approval_details = (
        f'{escape(approval_title).replace(chr(10), "<br/>")}<br/>'
        f'{JARMO_COMPANY}<br/><b>Date:</b> '
        f'{escape(_value(approval["date"], "__________________________"))}'
    )
    approval_identity = Spacer(1, 0) if source_stream else Paragraph(
        f'<b>{escape(approval_name)}</b><br/>{approval_details}', preview,
    )
    signing_block = approval_identity
    name_offset = 0
    column_style = TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ])
    if source_stream:
        # The signed source can have overlapping signature, seal and date.
        # Preserve that complete buyer block instead of reconstructing it.
        approved.extend([Spacer(1, 2 * mm), _approval_image(source_stream, 79, 65)])
    elif signature_stream:
        signature_image = _approval_image(signature_stream, 42 if stamp_stream else 52, 20)
        name_offset = signature_image.drawHeight + mm
        signing_block = Table([[[signature_image, Spacer(1, mm), approval_identity]]],
                              colWidths=[79 * mm], style=column_style)
        if stamp_stream:
            # Center the seal beside the signature, with the name immediately
            # below the signature. Flow the remaining identity beneath both
            # columns so the seal can never cover the title/company/date.
            stamp_image = _approval_image(stamp_stream, 30, 30)
            signature_inset = max(0, (stamp_image.drawHeight - signature_image.drawHeight) / 2)
            name_offset += signature_inset
            signing_style = TableStyle(column_style.getCommands() + [
                ('SPAN', (0, 1), (-1, 1)), ('TOPPADDING', (0, 1), (-1, 1), mm),
            ])
            signing_block = Table([
                [[Spacer(1, signature_inset), signature_image, Spacer(1, mm),
                  Paragraph(f'<b>{escape(approval_name)}</b>', preview)], '', stamp_image],
                [Paragraph(approval_details, preview), '', ''],
            ], colWidths=[42 * mm, mm, 36 * mm], style=signing_style)
    else:
        approved.append(Spacer(1, 16 * mm))
    if signature_issue:
        approved.append(Paragraph(escape(signature_issue), preview))
    raw_seller_reference = str(getattr(order, 'seller_reference', '') or '').strip()
    raw_contact_person = str(getattr(order, 'seller_contact_person', '') or '').strip()
    confirmation_reference = (
        raw_seller_reference
        if raw_contact_person and raw_seller_reference != raw_contact_person
        else ''
    )
    confirmation_rows = [
        ('Seller Signature', ' '),
        ('Date', _date_text(getattr(order, 'confirmation_date', None))),
        ('Seller Name', _value(getattr(vendor, 'name', None))),
        ('Seller Ref. no', _value(confirmation_reference)),
        ('Contact Person', raw_contact_person),
        ('Phone Number', _value(getattr(order, 'seller_phone', None))),
        ('Fax', _value(getattr(order, 'seller_fax', None))),
        ('Email', _value(getattr(order, 'seller_email', None))),
    ]
    confirmation = [
        Paragraph(
            '<b>Order Confirmation:</b><br/>We acknowledge receipt of your documents and will perform according to this PO.',
            preview,
        ),
        Spacer(1, 2 * mm),
        _FlowTable(
            [[Paragraph(f'<b>{escape(label)}:</b>', preview),
              Paragraph(escape(value).replace('\n', '<br/>'), preview)]
             for label, value in confirmation_rows],
            colWidths=[30 * mm, 49 * mm],
            splitInRow=1,
            style=TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('LINEBELOW', (1, 0), (1, 0), 0.6, colors.HexColor('#64748b')),
            ]),
        ),
    ]
    approval_style = TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBEFORE', (2, 0), (2, 0), 0.5, colors.HexColor('#64748b')),
        ('LEFTPADDING', (0, 0), (0, 0), 0), ('RIGHTPADDING', (0, 0), (0, 0), 7 * mm),
        ('LEFTPADDING', (2, 0), (2, 0), 3 * mm), ('RIGHTPADDING', (2, 0), (2, 0), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ])
    cover = [
        Spacer(1, 1 * mm), details, Spacer(1, 1 * mm), commercial, Spacer(1, 2 * mm),
        summary_table, Spacer(1, 2 * mm),
    ]
    # Keep the confirmation panel's height, but bring the approver identity/date
    # into the signing area rather than anchoring them just above the footer.
    # Measure actual text/signature flow so dense covers never overlap.
    cover_width = 176 * mm
    frame_width, frame_height = document.width - 12, document.height - 12
    width_scale = max(1, cover_width / frame_width)
    preceding_height = sum(item.wrap(cover_width, 1e6)[1] for item in cover)
    approval_table = Table(
        [[[*approved, signing_block], '', confirmation]],
        colWidths=[86 * mm, 5 * mm, 85 * mm], style=approval_style,
    )
    natural_height = approval_table.wrap(cover_width, 1e6)[1]
    panel_height = max(natural_height, frame_height * width_scale - preceding_height)
    identity_height = approval_identity.wrap(79 * mm, 1e6)[1]
    approved_height = Table([[approved]], colWidths=[79 * mm], style=column_style).wrap(79 * mm, 1e6)[1]
    signing_height = signing_block.wrap(79 * mm, 1e6)[1]
    panel_height = max(panel_height, approved_height + signing_height + 6)
    signing_top = max(approved_height,
                      panel_height - 6 - identity_height - 40 * mm * width_scale - name_offset)
    lower_space = max(0, panel_height - 6 - signing_top - signing_height)
    approved_column = Table(
        [[approved], [signing_block], ['']], colWidths=[79 * mm],
        rowHeights=[signing_top, signing_height, lower_space], style=column_style,
    )
    cover.append(Table(
        [[approved_column, '', confirmation]], colWidths=[86 * mm, 5 * mm, 85 * mm],
        rowHeights=[panel_height], style=approval_style,
    ))
    # Measure the complete cover and proportionally fit unusually long values
    # inside the first-page frame. Never clip text or move approval/confirmation
    # to a continuation page. The frame reserves the branded header and footer;
    # SimpleDocTemplate's default frame adds 6pt padding on each side.
    fitted_cover = KeepInFrame(document.width - 12, document.height - 12, cover,
                               mode='shrink', hAlign='CENTER', vAlign='TOP', fakeWidth=False)
    if measure_cover:
        # Word uses the same measured approval panel and identity position.
        # This path measures existing flowables without serializing a PDF or
        # downloading supporting attachments.
        from reportlab.pdfgen.canvas import Canvas

        fitted_cover.wrapOn(Canvas(BytesIO()), document.width - 12, document.height - 12)
        scale = getattr(fitted_cover, '_scale', 1)
        return {
            'panel_height': panel_height / scale,
            'identity_gap': max(0, signing_top - approved_height) / scale,
            'scale': scale,
        }
    story = [fitted_cover]
    narrative = _scope_narrative_blocks(order.description)
    if introduction or narrative:
        story.extend((
            PageBreak(),
            Paragraph(f'<u>PURCHASE ORDER:</u> &nbsp;{escape(_value(order.title))}', styles['heading']),
        ))
        if introduction:
            story.append(Paragraph(introduction, styles['preview']))
        if show_scope_heading(order):
            story.append(Paragraph('PO DESCRIPTION &amp; SCOPE', styles['heading']))
        story.extend(_pdf_rich_text(narrative, styles, width=document.width - 12))
    # Match the live A4 document: the price summary starts on a clean page.
    # This also prevents an orphaned heading or split table after long scope text.
    story.extend((PageBreak(), Paragraph('SUMMARY OF PRICES', styles['heading'])))
    item_columns = _item_columns(order)
    price_rows = [[Paragraph(f'<b>{escape(title)}</b>', styles['small']) for _, title in item_columns]]
    for item in items:
        price_rows.append([Paragraph(escape(_item_value(item, key, currency)), styles['small']) for key, _ in item_columns])
    if item_columns:
        weights = [{'description': 3, 'specification': 2, 'comment': 2}.get(key, 1) for key, _ in item_columns]
        story.append(Table(price_rows, repeatRows=1, colWidths=[176 * mm * weight / sum(weights) for weight in weights], style=TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 1.2, colors.HexColor('#475569')),
            ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.HexColor('#475569')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ])))
    story.extend([
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
    story = [
        Spacer(1, 55 * mm),
        Paragraph(f'ATTACHMENT - {index + 1}', styles['cover']),
        Spacer(1, 12 * mm),
    ]
    title = str(attachment.get('title') or '').strip()
    description = str(attachment.get('description') or '').strip()
    if title and title.casefold() != f'attachment {index + 1}'.casefold():
        story.extend((Paragraph(escape(title), styles['cover_body']), Spacer(1, 5 * mm)))
    if description and description != title:
        story.append(Paragraph(escape(description), styles['cover_body']))
    document.build(story, onFirstPage=lambda canvas, doc: _pdf_page(canvas, doc, order, page_number))
    return output.getvalue()


def _download_attachment(attachment):
    from django.conf import settings
    from django.core.files.storage import default_storage

    # Only server-created preview snapshots carry bytes. Public request
    # metadata cannot supply this type, and no storage mutation is needed.
    if isinstance(attachment.get('_preview_content'), bytes):
        return attachment['_preview_content']
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


def _purchase_order_pdf_packet(order):
    """Build the canonical packet and retain its authored-body boundary."""
    writer = PdfWriter()
    warnings = []
    source_readers = []

    def append(pdf_bytes):
        reader = PdfReader(BytesIO(pdf_bytes))
        # PyPDF2 caches imported references by id(reader). Keep every source
        # alive through write(), so garbage collection cannot recycle an ID
        # and substitute another document's content/resources in a later page.
        source_readers.append(reader)
        for page in reader.pages:
            writer.add_page(page)

    append(_main_pdf(order))
    body_page_count = len(writer.pages)
    renderable_attachments = [row for row in (order.attachments or []) if not (
        isinstance(row, dict) and row.get('type') == 'po_excel_import_source'
        and not any(row.get(key) for key in ('s3_key', 'storage_key', 'url', 's3_url', 'file_url'))
    )]
    source_keys = None
    for index, raw_attachment in enumerate(renderable_attachments):
        attachment = _attachment(raw_attachment, index)
        if attachment.get('type') == 'signed_purchase_order_pdf' and attachment.get('document_id'):
            # Signed imports store their private key on the confirmed source
            # document. Resolve that binding rather than fetching a public URL
            # or silently dropping the original from preview and download.
            if source_keys is None:
                source_keys = {
                    source['id']: source['storage_key']
                    for source in uploaded_purchase_order_sources(order)
                    if is_safe_source_storage_key(source['storage_key'])
                } if getattr(order, 'pk', None) else {}
            attachment['s3_key'] = source_keys.get(str(attachment['document_id']), '')
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
    return output.getvalue(), warnings, body_page_count


def build_purchase_order_pdf(order):
    """Return one PDF containing the PO, attachment covers, and renderable files."""
    content, warnings, _ = _purchase_order_pdf_packet(order)
    return content, warnings


def _docx_cell_shading(cell, fill):
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn('w:shd'))
    if shading is None:
        shading = OxmlElement('w:shd')
        properties.append(shading)
    shading.set(qn('w:fill'), fill)


def _docx_set_cell_text(cell, value, *, size=9.5, bold=False, color='334155', align=None):
    cell.text = ''
    paragraph = cell.paragraphs[0]
    if align is not None:
        paragraph.alignment = align
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.line_spacing = Pt(size + 2)
    run = paragraph.add_run(str(value or ''))
    run.bold = bold
    run.font.name = 'Arial'
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    return paragraph


def _docx_add_page_field(paragraph):
    run = paragraph.add_run()
    run.font.name = 'Arial'
    run.font.size = Pt(7)
    run.font.color.rgb = RGBColor.from_string('3275B6')
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    instruction = OxmlElement('w:instrText')
    instruction.set(qn('xml:space'), 'preserve')
    instruction.text = ' PAGE '
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    run._r.extend((begin, instruction, end))


def _docx_no_borders(table):
    properties = table._tbl.tblPr
    borders = properties.first_child_found_in('w:tblBorders')
    if borders is None:
        borders = OxmlElement('w:tblBorders')
        properties.append(borders)
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        element = OxmlElement(f'w:{edge}')
        element.set(qn('w:val'), 'nil')
        borders.append(element)


def _docx_cell_margins(cell, *, top=0, left=0, bottom=0, right=0):
    properties = cell._tc.get_or_add_tcPr()
    old = properties.find(qn('w:tcMar'))
    if old is not None:
        properties.remove(old)
    margins = OxmlElement('w:tcMar')
    for side, value in (('top', top), ('left', left), ('bottom', bottom), ('right', right)):
        edge = OxmlElement(f'w:{side}')
        edge.set(qn('w:w'), str(round(value * 20)))
        edge.set(qn('w:type'), 'dxa')
        margins.append(edge)
    properties.append(margins)


def _docx_cell_border(cell, edge, *, color='475569', size=8):
    properties = cell._tc.get_or_add_tcPr()
    borders = properties.find(qn('w:tcBorders'))
    if borders is None:
        borders = OxmlElement('w:tcBorders')
        properties.append(borders)
    border = OxmlElement(f'w:{edge}')
    border.set(qn('w:val'), 'single')
    border.set(qn('w:color'), color)
    border.set(qn('w:sz'), str(size))
    borders.append(border)


def _docx_table(container, widths, rows=1):
    table = container.add_table(rows=rows, cols=len(widths))
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    _docx_no_borders(table)
    for column, width in zip(table.columns, widths):
        column.width = Mm(width)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Mm(width)
            _docx_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    return table


def _docx_spacer(container, height):
    paragraph = container.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = Mm(height)
    paragraph.add_run().font.size = Pt(1)
    return paragraph


def _docx_field_rows(container, rows, widths=(30, 52)):
    table = _docx_table(container, widths, rows=len(rows))
    for row, (label, value, strong) in zip(table.rows, rows):
        _docx_set_cell_text(row.cells[0], label + ':', bold=True)
        _docx_set_cell_text(row.cells[1], _value(value), bold=strong)
        for cell in row.cells:
            _docx_cell_margins(cell, top=1.5, bottom=2, right=4.25)
    return table


def _docx_empty_cell_paragraphs(cell):
    # Word requires a final paragraph after a nested table. Keep that required
    # paragraph negligible, rather than introducing an extra line per panel.
    for paragraph in cell.paragraphs:
        if not paragraph.text:
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = Pt(1)
            paragraph.add_run().font.size = Pt(1)


def _docx_heading(document, text, *, prefix=''):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(8)
    paragraph.paragraph_format.space_after = Pt(5)
    paragraph.paragraph_format.line_spacing = Pt(15)
    paragraph.paragraph_format.keep_with_next = True
    if prefix:
        run = paragraph.add_run(prefix)
        run.underline = True
        run.bold = True
    run = paragraph.add_run(text)
    run.bold = True
    for run in paragraph.runs:
        run.font.name = 'Arial'
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor.from_string('1F2937')
    return paragraph


def _configure_docx_header_footer(document, order):
    section = document.sections[0]
    section.header_distance = Mm(5.5)
    section.footer_distance = Mm(15)

    header = section.header
    table = header.add_table(rows=1, cols=2, width=Mm(178))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.columns[0].width = Mm(135)
    table.columns[1].width = Mm(43)
    _docx_no_borders(table)
    left, right = table.rows[0].cells
    left.width, right.width = Mm(135), Mm(43)
    for cell in (left, right):
        _docx_cell_margins(cell)
    paragraph = _docx_set_cell_text(left, 'PURCHASE ORDER', size=15, bold=True, color='3275B6')
    for value, size, bold, color in (
        (_value(order.po_number, 'PO NUMBER PENDING'), 10, True, '3275B6'),
        (_value(getattr(order, 'form_note', None), '(PO no. to be used in all documents)'), 7.5, False, '64748B'),
        (_date_text(getattr(order, 'po_date', None)), 10, True, '3275B6'),
    ):
        paragraph = left.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = Pt(12 if size == 7.5 else 16)
        run = paragraph.add_run(value)
        run.bold = bold
        run.font.name = 'Arial'
        run.font.size = Pt(size)
        run.font.color.rgb = RGBColor.from_string(color)
    right.text = ''
    logo_paragraph = right.paragraphs[0]
    logo_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    logo_paragraph.paragraph_format.space_after = Pt(0)
    logo_paragraph.paragraph_format.space_before = Mm(2)
    if LOGO_PATH.exists():
        logo_paragraph.add_run().add_picture(str(LOGO_PATH), width=Mm(27))
    tagline = logo_paragraph.add_run('\nHOME OF THE\nLEARNING MINDS')
    tagline.bold = True
    tagline.font.name = 'Arial'
    tagline.font.size = Pt(8.5)
    tagline.font.color.rgb = RGBColor.from_string('3275B6')

    footer = section.footer
    band = footer.add_table(rows=1, cols=5, width=Mm(178))
    band.alignment = WD_TABLE_ALIGNMENT.CENTER
    band.autofit = False
    _docx_no_borders(band)
    band.rows[0].height = Mm(7)
    band.rows[0].height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
    values = ('REJLERS', 'HOME of the\nLEARNING MINDS', 'REJLERS', 'HOME of the\nLEARNING MINDS', 'REJLERS')
    for index, (cell, value) in enumerate(zip(band.rows[0].cells, values)):
        _docx_cell_shading(cell, '0870AA')
        _docx_cell_margins(cell)
        cell.width = Mm(35.6)
        paragraph = _docx_set_cell_text(cell, value, size=7, bold=True, color='FFFFFF', align=WD_ALIGN_PARAGRAPH.CENTER)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        if index in (0, 2, 4) and WHITE_LOGO_PATH.exists():
            paragraph.clear()
            paragraph.add_run().add_picture(str(WHITE_LOGO_PATH), width=Mm(21))
    # Separate native tables so Word does not merge their grids and let the
    # address-cell inset resize the branded band above it.
    footer.add_paragraph()
    details = footer.add_table(rows=1, cols=2, width=Mm(178))
    details.alignment = WD_TABLE_ALIGNMENT.CENTER
    details.autofit = False
    _docx_no_borders(details)
    details.columns[0].width, details.columns[1].width = Mm(140), Mm(38)
    details.cell(0, 0).width, details.cell(0, 1).width = Mm(140), Mm(38)
    for cell in details.rows[0].cells:
        _docx_cell_margins(cell, top=3)
    _docx_cell_margins(details.cell(0, 0), top=3, left=Mm(8).pt)
    _docx_set_cell_text(
        details.cell(0, 0),
        f'{COMPANY_NAME}\nRejlers Tower, 13th floor, AI Hamdan Street, P.O. Box 39317,\n'
        f'Abu Dhabi, United Arab Emirates\nTel: {COMPANY_PHONE} | {COMPANY_WEBSITE}',
        size=7, color='3275B6',
    )
    page_paragraph = _docx_set_cell_text(details.cell(0, 1), 'Page ', size=7, color='3275B6', align=WD_ALIGN_PARAGRAPH.RIGHT)
    details.cell(0, 1).vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
    _docx_add_page_field(page_paragraph)
    for area in (header, footer):
        for paragraph in area.paragraphs:
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = Pt(1)
            paragraph.add_run().font.size = Pt(1)


def build_purchase_order_docx(order, *, with_warnings=False):
    """Return the editable company PO with canonical supporting pages as images.

    Existing service callers receive bytes. HTTP downloads request warnings so
    unavailable supporting files are disclosed just as they are for the PDF.
    """
    document = Document()
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    # The PDF frame includes 6pt padding inside its 16mm/34mm/42mm margins.
    # Keep the editable Word body on that same A4 content rectangle.
    section.top_margin = Mm(34) + Pt(6)
    section.bottom_margin = Mm(42) + Pt(6)
    section.left_margin = Mm(16) + Pt(6)
    section.right_margin = Mm(16) + Pt(6)
    normal_style = document.styles['Normal']
    normal_style.font.name = 'Arial'
    normal_style.font.size = Pt(10.5)
    normal_style.paragraph_format.space_after = Pt(0)
    normal_style.paragraph_format.line_spacing = Pt(13.5)
    _configure_docx_header_footer(document, order)
    cover_metrics = _main_pdf(order, measure_cover=True)

    vendor = getattr(order, 'vendor', None)
    invoice_emails = getattr(order, 'invoicing_emails', None) or []
    if not isinstance(invoice_emails, (list, tuple)):
        invoice_emails = [invoice_emails]
    invoice_address = DEFAULT_INVOICE_ADDRESS
    if getattr(order, 'invoicing_attn', None) or invoice_emails:
        invoice_address = '\n'.join(filter(None, (
            str(getattr(order, 'invoicing_attn', '') or '').strip(),
            *[str(email).strip() for email in invoice_emails if email],
            'Rejlers International Engineering Solutions AB', 'PO Box 39317',
            'Abu Dhabi, UAE.', 'Tel: +971 2 639 7449',
            f'Fax: {_value(getattr(order, "company_fax", None), "+971 2 639 7448")}',
        )))
    _docx_spacer(document, 1)
    details = _docx_table(document, (83, 8, 83))
    seller_fields = _docx_field_rows(details.cell(0, 0), (
        ('Seller', getattr(vendor, 'name', None), False),
        ('Seller Address', getattr(order, 'seller_address', None) or getattr(vendor, 'address', None), False),
        ('Invoicing Address', invoice_address, False),
    ), widths=(30, 53))
    invoice_cell = seller_fields.cell(2, 1)
    invoice_cell.text = ''
    invoice_paragraph = invoice_cell.paragraphs[0]
    invoice_paragraph.paragraph_format.line_spacing = Pt(11.5)
    for index, line in enumerate(invoice_address.splitlines()):
        if index:
            invoice_paragraph.add_run('\n')
        run = invoice_paragraph.add_run(line)
        run.font.name = 'Arial'
        run.font.size = Pt(9 if '@' in line else 9.5)
        run.font.color.rgb = RGBColor.from_string('334155')
    references = _docx_field_rows(details.cell(0, 2), (
        ('Seller Reference', getattr(order, 'seller_reference', None), False),
        ('Quote Ref.', getattr(order, 'quote_ref', None), False),
        ('License No.', getattr(order, 'seller_license_no', None), False),
        ('Buyer Reference', '', False),
    ), widths=(30, 53))
    reference_cell = references.cell(3, 1)
    # Shared markup is safe, generated display content. Preserve its line
    # breaks/bold job titles, rather than printing <br/> and <b> in Word.
    reference_cell.text = ''
    paragraph = reference_cell.paragraphs[0]
    paragraph.paragraph_format.line_spacing = Pt(11.5)
    for index, line in enumerate(_buyer_reference(order).split('<br/>')):
        if index:
            paragraph.add_run('\n')
        run = paragraph.add_run(html.unescape(strip_tags(line)))
        run.bold = '<b>' in line
        run.font.name = 'Arial'
        run.font.size = Pt(9 if '@' in line else 9.5)
        run.font.color.rgb = RGBColor.from_string('334155')
    for cell in details.rows[0].cells:
        _docx_empty_cell_paragraphs(cell)

    _docx_spacer(document, 1)
    commercial = _docx_table(document, (30, 53, 8, 30, 53), rows=3)
    commercial_rows = (
        ('Payment Terms:', getattr(order, 'payment_terms', None), '', 'Delivery terms:', getattr(order, 'delivery_terms', None)),
        ('Payment Mode:', getattr(order, 'payment_mode', None), '', 'Delivery date:', _date_text(getattr(order, 'expected_delivery', None))),
        ('Project:', _project_display(order), '', 'Marking:', getattr(order, 'marking', None) or order.po_number),
    )
    for row_index, (row, values) in enumerate(zip(commercial.rows, commercial_rows)):
        for index, value in enumerate(values):
            _docx_set_cell_text(row.cells[index], '' if index == 2 else _value(value), bold=index in (0, 3) or row_index == 2)
            _docx_cell_margins(row.cells[index], top=1.5, bottom=2, right=4.25)

    _docx_spacer(document, 2)
    summary = _docx_table(document, (89, 6, 79))
    for cell in summary.rows[0].cells:
        _docx_cell_border(cell, 'top', size=10)
        _docx_cell_border(cell, 'bottom', size=10)
        _docx_cell_margins(cell, top=4.25, bottom=4.25)
    _docx_set_cell_text(summary.cell(0, 0), f'Purchase Summary:\n{_value(_purchase_summary(order))}', bold=True)
    subtotal_for_summary = float(order.net_amount) if getattr(order, 'net_amount', None) is not None else sum(item['total'] for item in _items(order))
    summary_totals = _docx_table(summary.cell(0, 2), (45, 34), rows=3)
    for row, (label, value) in zip(summary_totals.rows, (
        ('Total Purchase Price:', f'{subtotal_for_summary:,.2f} {order.currency or "AED"}'),
        (f'VAT ({float(order.vat_percentage or 0):g}%):', f'{float(order.tax_amount or 0):,.2f} {order.currency or "AED"}'),
        ('Total Sum:', f'{float(order.total_amount or 0):,.2f} {order.currency or "AED"}'),
    )):
        _docx_set_cell_text(row.cells[0], label, bold=True)
        _docx_set_cell_text(row.cells[1], value, size=8.5, align=WD_ALIGN_PARAGRAPH.RIGHT)
    _docx_empty_cell_paragraphs(summary.cell(0, 2))

    _docx_spacer(document, 2)
    approval = _docx_table(document, (85, 5, 84))
    approval.rows[0].height = Pt(cover_metrics['panel_height'])
    approval.rows[0].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    _docx_cell_border(approval.cell(0, 2), 'left', color='64748B', size=4)
    _docx_cell_margins(approval.cell(0, 0), top=3, right=19.8)
    _docx_cell_margins(approval.cell(0, 2), top=3, left=8.5)
    approval_cell = approval.cell(0, 0)
    # Word already flows the signing block directly below the cover summary;
    # make that upper placement explicit even when confirmation is taller.
    approval_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    approval_display = _approval_display(order)
    signature_issue = purchase_order_signature_issue(order)
    signature_stream, stamp_stream, source_stream = _approval_artwork(order, approval_display, signature_issue)
    heading = '' if source_stream else f'\n{approval_display["heading"]}'
    _docx_set_cell_text(approval_cell, f'PO status: {approval_display["status"]}{heading}', bold=True)
    approval_name, approval_title = approval_display['name'], approval_display['title']
    identity_with_artwork = False
    if not source_stream:
        _docx_spacer(approval_cell, cover_metrics['identity_gap'] / mm)
    if source_stream:
        source_image = _approval_image(source_stream, 79, 65)
        source_stream.seek(0)
        approval_cell.add_paragraph().add_run().add_picture(
            source_stream, width=Pt(source_image.drawWidth), height=Pt(source_image.drawHeight),
        )
    elif signature_stream:
        signature_image = _approval_image(signature_stream, 42 if stamp_stream else 50, 20)
        signature_stream.seek(0)
        if stamp_stream:
            stamp_image = _approval_image(stamp_stream, 30, 30)
            stamp_stream.seek(0)
            signing = approval_cell.add_table(rows=2, cols=3)
            signing.autofit = False
            _docx_no_borders(signing)
            for column, width in zip(signing.columns, (42, 1, 36)):
                column.width = Mm(width)
                for cell in column.cells:
                    cell.width = Mm(width)
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                    margins = OxmlElement('w:tcMar')
                    for side in ('top', 'left', 'bottom', 'right'):
                        margin = OxmlElement(f'w:{side}')
                        margin.set(qn('w:w'), '0')
                        margin.set(qn('w:type'), 'dxa')
                        margins.append(margin)
                    cell._tc.get_or_add_tcPr().append(margins)
            paragraph = signing.cell(0, 0).paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(max(0, (stamp_image.drawHeight - signature_image.drawHeight) / 2))
            paragraph.paragraph_format.space_after = Mm(1)
            paragraph.add_run().add_picture(signature_stream, width=Pt(signature_image.drawWidth), height=Pt(signature_image.drawHeight))
            name = signing.cell(0, 0).add_paragraph()
            name.paragraph_format.space_after = Pt(0)
            name.add_run(approval_name).bold = True
            paragraph = signing.cell(0, 2).paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.add_run().add_picture(stamp_stream, width=Pt(stamp_image.drawWidth), height=Pt(stamp_image.drawHeight))
            details = signing.cell(1, 0).merge(signing.cell(1, 2))
            _docx_set_cell_text(details, f'{approval_title}\n{JARMO_COMPANY}\nDate: {_value(approval_display["date"], "__________________________")}')
            identity_with_artwork = True
        else:
            paragraph = approval_cell.add_paragraph()
            paragraph.paragraph_format.space_after = Mm(1)
            paragraph.add_run().add_picture(signature_stream, width=Pt(signature_image.drawWidth), height=Pt(signature_image.drawHeight))
    else:
        # The PDF reserves a blank signature area before its measured gap.
        _docx_spacer(approval_cell, 16 / cover_metrics['scale'])
    if signature_issue:
        approval_cell.add_paragraph(signature_issue)
    if not source_stream and not identity_with_artwork:
        identity = approval_cell.add_paragraph()
        identity.add_run(approval_name).bold = True
        identity.add_run(f'\n{approval_title}\n{JARMO_COMPANY}\n')
        identity.add_run('Date: ').bold = True
        identity.add_run(_value(approval_display['date'], '__________________________'))
        identity.paragraph_format.line_spacing = Pt(11.5)
        for run in identity.runs:
            run.font.size = Pt(9.5)
            run.font.color.rgb = RGBColor.from_string('334155')
    confirmation = approval.cell(0, 2)
    _docx_set_cell_text(confirmation, 'Order Confirmation:\nWe acknowledge receipt of your documents and will perform according to this PO.')
    _docx_spacer(confirmation, 2)
    seller_reference = str(getattr(order, 'seller_reference', '') or '').strip()
    seller_contact = str(getattr(order, 'seller_contact_person', '') or '').strip()
    seller_reference = seller_reference if seller_contact and seller_reference != seller_contact else ''
    confirmation_fields = _docx_field_rows(confirmation, (
        ('Seller Signature', '________________________', False),
        ('Date', _date_text(getattr(order, 'confirmation_date', None)), False),
        ('Seller Name', getattr(vendor, 'name', None), False),
        ('Seller Ref. no', seller_reference, False),
        ('Contact Person', seller_contact, False),
        ('Phone Number', getattr(order, 'seller_phone', None), False),
        ('Fax', getattr(order, 'seller_fax', None), False),
        ('Email', getattr(order, 'seller_email', None), False),
    ), widths=(30, 49))
    # A missing contact name remains blank, as in the PDF confirmation panel.
    _docx_set_cell_text(confirmation_fields.cell(4, 1), seller_contact)
    _docx_empty_cell_paragraphs(confirmation)
    # A heading page break does not leave an empty extra page when the cover
    # panel fills its A4 frame, unlike a separate page-break paragraph.
    introduction_text = purchase_order_introduction(order)
    narrative = _scope_narrative_blocks(order.description)
    if introduction_text or narrative:
        _docx_heading(document, f'  {_value(order.title)}', prefix='PURCHASE ORDER:').paragraph_format.page_break_before = True
        if introduction_text:
            introduction = document.add_paragraph()
            contacts = getattr(order, 'contact_persons', None)
            if isinstance(contacts, dict) and isinstance(contacts.get(INTRODUCTION_KEY), str):
                introduction.add_run(introduction_text)
            else:
                introduction.add_run(f'We, {COMPANY_NAME} (Buyer), issue this purchase order to ')
                introduction.add_run(_value(getattr(vendor, 'name', None))).bold = True
                introduction.add_run(' (Seller).')
            for run in introduction.runs:
                run.font.color.rgb = RGBColor.from_string('334155')
        if show_scope_heading(order):
            _docx_heading(document, 'PO DESCRIPTION & SCOPE')
        _docx_rich_text(document, narrative)
    _docx_heading(document, 'SUMMARY OF PRICES').paragraph_format.page_break_before = True
    items = _items(order)
    item_columns = _item_columns(order)
    weights = [{'description': 3, 'specification': 2, 'comment': 2}.get(key, 1) for key, _ in item_columns]
    widths = [174 * weight / sum(weights) for weight in weights]
    table = _docx_table(document, widths, rows=len(items) + 1) if item_columns else None
    if table is not None:
        table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        for cell, (_, heading) in zip(table.rows[0].cells, item_columns):
            _docx_set_cell_text(cell, heading, size=7, bold=True, color='000000')
            _docx_cell_border(cell, 'top', size=10)
            _docx_cell_border(cell, 'bottom', size=10)
    currency = order.currency or 'AED'
    for index, item in enumerate(items):
        if table is None:
            break
        cells = table.rows[index + 1].cells
        values = [_item_value(item, key, currency) for key, _ in item_columns]
        for cell, value in zip(cells, values):
            _docx_set_cell_text(cell, str(value), size=7, color='000000')
    if table is not None:
        for row in table.rows:
            for cell in row.cells:
                _docx_cell_margins(cell, left=3, right=3, top=4, bottom=4)
    subtotal = float(order.net_amount) if getattr(order, 'net_amount', None) is not None else sum(item['total'] for item in items)
    tax = float(order.tax_amount or 0)
    total = float(order.total_amount or subtotal + tax)
    _docx_spacer(document, 6)
    totals = _docx_table(document, (55, 42), rows=3)
    totals.alignment = WD_TABLE_ALIGNMENT.RIGHT
    for index, (row, values) in enumerate(zip(totals.rows, (
        ('Total Price:', _money(subtotal, currency)),
        (f'VAT ({float(order.vat_percentage or 0):g}%):', _money(tax, currency)),
        ('Total Sum:', _money(total, currency)),
    ))):
        for column, (cell, value) in enumerate(zip(row.cells, values)):
            _docx_set_cell_text(cell, value, size=8.5, bold=True, color='000000',
                                align=WD_ALIGN_PARAGRAPH.RIGHT if column else WD_ALIGN_PARAGRAPH.LEFT)
            _docx_cell_margins(cell, left=6, right=6, top=3, bottom=3)
            for edge in ('top', 'left', 'bottom', 'right'):
                _docx_cell_border(cell, edge, size=4)
            if index == 2:
                _docx_cell_shading(cell, 'E2E8F0')
    warnings = []
    if order.attachments:
        packet, warnings, body_page_count = _purchase_order_pdf_packet(order)
        append_pdf_pages(document, packet, start_page=body_page_count)
    output = BytesIO()
    document.save(output)
    content = output.getvalue()
    return (content, warnings) if with_warnings else content
