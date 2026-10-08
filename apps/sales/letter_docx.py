"""DOCX generation for sales letters (python-docx).

Mirrors the standard Rejlers letterhead produced by the PDF renderer so the
downloadable Word copy can be edited offline without losing the layout.
"""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from .letter_context import build_letter_context, split_body_paragraphs
from .letter_defaults import get_logo_path

logger = logging.getLogger(__name__)

DOCX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _strip_table_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        element = OxmlElement(f'w:{edge}')
        element.set(qn('w:val'), 'none')
        element.set(qn('w:sz'), '0')
        borders.append(element)
    tbl_pr.append(borders)


def _set_cell_width(cell, width_mm):
    cell.width = Mm(width_mm)


def _add_run(paragraph, text, *, bold=False):
    run = paragraph.add_run(text)
    run.bold = bold
    return run


def _meta_row(table, row_idx, label, lines):
    label_cell = table.cell(row_idx, 0)
    value_cell = table.cell(row_idx, 1)
    label_cell.text = ''
    value_cell.text = ''
    _add_run(label_cell.paragraphs[0], label, bold=True)
    first = True
    for line in lines:
        paragraph = value_cell.paragraphs[0] if first else value_cell.add_paragraph()
        _add_run(paragraph, line)
        first = False
    return label_cell, value_cell


def _add_bottom_border(paragraph):
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), '666666')
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def generate_letter_docx(letter, custom_data=None):
    """Generate DOCX bytes for a letter."""
    merged = dict(letter.custom_data or {})
    merged.update(custom_data or {})
    context = build_letter_context(letter.opportunity, letter=letter, custom_data=merged)
    lh = context['letterhead']

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(20), Mm(22)
    section.left_margin, section.right_margin = Mm(20), Mm(20)

    normal = document.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.size = Pt(10.5)

    # Header: response/confidential left, logo right.
    header_table = document.add_table(rows=1, cols=2)
    _strip_table_borders(header_table)
    _set_cell_width(header_table.cell(0, 0), 120)
    _set_cell_width(header_table.cell(0, 1), 50)
    left = header_table.cell(0, 0)
    left.text = ''
    p = left.paragraphs[0]
    _add_run(p, lh['response_label'], bold=True)
    p.add_run('\t')
    _add_run(p, lh['response_code'])
    p2 = left.add_paragraph()
    _add_run(p2, 'Confidential', bold=True)
    p2.add_run('\t')
    _add_run(p2, lh['confidential'])
    right = header_table.cell(0, 1)
    right.text = ''
    logo_path = context.get('logo_path') or get_logo_path()
    if logo_path:
        right.paragraphs[0].add_run().add_picture(str(logo_path), height=Mm(13))
    else:
        run = _add_run(right.paragraphs[0], 'REJLERS', bold=True)
        run.font.size = Pt(24)

    # Meta block.
    recipient_lines = [lh['recipient_name']]
    if lh.get('recipient_title'):
        recipient_lines.append(lh['recipient_title'])
    recipient_lines.append(lh['recipient_company'])
    if lh.get('recipient_address'):
        recipient_lines.append(lh['recipient_address'])
    if lh.get('recipient_email'):
        recipient_lines.append(f"E-mail: {lh['recipient_email']}")

    meta = document.add_table(rows=9, cols=2)
    _strip_table_borders(meta)
    rows = [
        ('From:', [lh['sender_name'], lh['sender_company']]),
        ('Bid Focal Contact:', [lh['focal_contact']]),
        ('Contact Number:', [lh['contact_number']]),
        ('Fax:', [lh['fax']]),
        ('Email:', [lh['email']]),
        ('To:', recipient_lines),
        ('Your Ref. No.:', [lh['your_ref'] or '—']),
        ('Our Ref. No.:', [lh['our_ref']]),
        ('Our ADNOC Unified Code:', [lh['adnoc_unified_code']]),
    ]
    for idx, (label, lines) in enumerate(rows):
        _meta_row(meta, idx, label, lines)
        _set_cell_width(meta.cell(idx, 0), 45)
        _set_cell_width(meta.cell(idx, 1), 125)

    # Subject.
    subject_p = document.add_paragraph()
    subject_p.paragraph_format.space_before = Pt(12)
    subject_p.paragraph_format.space_after = Pt(8)
    _add_run(subject_p, 'Subject: ', bold=True)
    _add_run(subject_p, context['subject'], bold=True)
    if context.get('subject_suffix'):
        suffix_p = document.add_paragraph()
        suffix_p.paragraph_format.space_after = Pt(8)
        _add_run(suffix_p, context['subject_suffix'], bold=True)

    # Body.
    for para in split_body_paragraphs(letter.body):
        p = document.add_paragraph()
        p.paragraph_format.space_after = Pt(8)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _add_run(p, para)

    # Signature block.
    signature_p = document.add_paragraph()
    signature_p.paragraph_format.space_before = Pt(16)
    _add_run(signature_p, lh['signature_name'], bold=True)
    for title in lh['signature_titles']:
        title_p = document.add_paragraph()
        title_p.paragraph_format.space_after = Pt(0)
        _add_run(title_p, title)

    # Footer separator + company footer.
    rule_p = document.add_paragraph()
    rule_p.paragraph_format.space_before = Pt(24)
    _add_bottom_border(rule_p)
    footer_lines = [lh['footer_company'], lh['footer_address'], lh['footer_contact']]
    for line in footer_lines:
        p = document.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        run = _add_run(p, line)
        run.font.size = Pt(8)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def save_letter_docx(letter, custom_data=None):
    """Generate and save the DOCX to the letter's docx_file field."""
    docx_bytes = generate_letter_docx(letter, custom_data)
    filename = f"{letter.opportunity.deal_code}-{letter.letter_type}-{letter.generated_at.strftime('%Y%m%d')}.docx"
    letter.docx_file.save(filename, ContentFile(docx_bytes), save=False)
    letter.docx_generated_at = timezone.now()
    letter.save(update_fields=['docx_file', 'docx_generated_at', 'updated_at'])
    logger.info(f"DOCX generated for letter {letter.id}: {filename}")
    return letter.docx_file.path


def regenerate_letter_docx(letter, custom_data=None):
    if letter.docx_file:
        try:
            letter.docx_file.delete(save=False)
        except Exception:
            pass
    return save_letter_docx(letter, custom_data)


def get_letter_docx_bytes(letter):
    """Get DOCX bytes (generates on demand if missing)."""
    if not letter.docx_file:
        save_letter_docx(letter)
    if letter.docx_file:
        letter.docx_file.open('rb')
        docx_bytes = letter.docx_file.read()
        letter.docx_file.close()
        return docx_bytes
    return generate_letter_docx(letter)
