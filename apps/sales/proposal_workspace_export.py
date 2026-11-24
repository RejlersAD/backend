"""Controlled PDF/DOCX rendering for local technical-proposal workspace versions."""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from rest_framework.exceptions import ValidationError

from .proposal_workspace_service import _quote

COMPANY_NAME = 'Rejlers International Engineering Solutions AB'


def _version(actor, quote_id, requested_version):
    quote = _quote(actor, quote_id)
    rows = quote.technical_proposal_versions.order_by('-version')
    if requested_version is not None:
        try:
            requested_version = int(requested_version)
        except (TypeError, ValueError):
            raise ValidationError({'version': 'Use an integer workspace version.'})
        row = rows.filter(version=requested_version).first()
    else:
        row = rows.first()
    if row is None:
        raise ValidationError({'workspace': 'Generate a proposal workspace before export.'})
    return quote, row


def _filename(quote, row, suffix):
    base = re.sub(r'[^A-Za-z0-9_-]+', '-', quote.quote_number).strip('-') or 'proposal'
    return f'{base}-technical-v{row.version}.{suffix}'


def workspace_docx(actor, quote_id, requested_version=None):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Inches, Pt, RGBColor

    quote, row = _version(actor, quote_id, requested_version)
    content = row.content
    if content.get('template_version') == 'rejlers_authored_v1':
        from .proposal_engine_service import stored_output
        return stored_output(actor, quote_id, row, 'master_docx')
    document = Document()
    section = document.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    normal = document.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.size = Pt(10)
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), 'Arial')
    for level in range(1, 4):
        heading = document.styles[f'Heading {level}']
        heading.font.name = 'Arial'
        heading.font.color.rgb = RGBColor(0, 82, 147)
        heading.element.rPr.rFonts.set(qn('w:eastAsia'), 'Arial')

    header = section.header.paragraphs[0]
    header.text = (
        f"Technical Proposal | {content.get('proposal_number') or quote.quote_number} "
        f"| Rev {row.version} | Confidential"
    )
    header.runs[0].font.name = 'Arial'
    header.runs[0].font.size = Pt(8)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(f"{COMPANY_NAME} | Page ").font.size = Pt(8)
    field = footer.add_run()
    for kind, instruction in (
        ('begin', None), (None, 'PAGE'), ('separate', None), ('end', None),
    ):
        element = (
            OxmlElement('w:fldChar') if kind
            else OxmlElement('w:instrText')
        )
        if kind:
            element.set(qn('w:fldCharType'), kind)
        else:
            element.set(qn('xml:space'), 'preserve')
            element.text = instruction
        field._r.append(element)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(content.get('title') or 'Technical Proposal')
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor(0, 82, 147)
    metadata = document.add_table(rows=0, cols=2)
    metadata.style = 'Table Grid'
    for key, label in (
        ('client_name', 'Client'), ('opportunity_reference', 'Opportunity reference'),
        ('proposal_number', 'Proposal number'), ('revision', 'Workspace revision'),
        ('issue_date', 'Issue date'),
    ):
        cells = metadata.add_row().cells
        cells[0].text = label
        cells[1].text = str(content.get(key) or '')
    document.add_page_break()

    for proposal_section in content.get('sections') or []:
        document.add_heading(proposal_section.get('title') or 'Section', level=1)
        if proposal_section.get('key') == 'toc':
            paragraph = document.add_paragraph()
            toc_field = paragraph.add_run()
            for kind, instruction in (
                ('begin', None), (None, 'TOC \\o "1-3" \\h \\z \\u'),
                ('separate', None), ('end', None),
            ):
                element = (
                    OxmlElement('w:fldChar') if kind
                    else OxmlElement('w:instrText')
                )
                if kind:
                    element.set(qn('w:fldCharType'), kind)
                else:
                    element.set(qn('xml:space'), 'preserve')
                    element.text = instruction
                toc_field._r.append(element)
        for paragraph in proposal_section.get('paragraphs') or []:
            text = str(paragraph.get('text') or '').strip()
            if text:
                document.add_paragraph(text)
            item = next((
                value for value in proposal_section.get('items') or []
                if value.get('id') == paragraph.get('id')
            ), None)
            if item and item.get('citation'):
                citation = document.add_paragraph()
                citation_run = citation.add_run(f'Source: {item["citation"]}')
                citation_run.italic = True
                citation_run.font.size = Pt(8)
        if proposal_section.get('rows'):
            table = document.add_table(rows=0, cols=max(len(row) for row in proposal_section['rows']))
            table.style = 'Table Grid'
            table.autofit = False
            column_count = max(len(row) for row in proposal_section['rows'])
            widths = {
                4: [Inches(.7), Inches(1.1), Inches(3.8), Inches(2.0)],
                6: [Inches(1.8), Inches(.7), Inches(1.0), Inches(1.4), Inches(.9), Inches(1.3)],
            }.get(column_count, [Inches(7.1 / column_count)] * column_count)
            for row_index, values in enumerate(proposal_section['rows']):
                cells = table.add_row().cells
                for column, value in enumerate(values):
                    cells[column].text = str(value)
                    cells[column].width = widths[column]
                    if row_index == 0:
                        for run in cells[column].paragraphs[0].runs:
                            run.bold = True
        document.add_page_break()

    update_fields = OxmlElement('w:updateFields')
    update_fields.set(qn('w:val'), 'true')
    document.settings.element.append(update_fields)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue(), _filename(quote, row, 'docx')


def workspace_pdf(actor, quote_id, requested_version=None):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus import Table, TableStyle

    quote, row = _version(actor, quote_id, requested_version)
    content = row.content
    if content.get('template_version') == 'rejlers_authored_v1':
        from .proposal_engine_service import stored_output
        return stored_output(actor, quote_id, row, 'client_pdf')
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'LocalProposalTitle', parent=styles['Title'], fontName='Helvetica-Bold',
        fontSize=20, leading=24, alignment=TA_CENTER, textColor=colors.HexColor('#005293'),
    ))
    styles.add(ParagraphStyle(
        'LocalProposalHeading', parent=styles['Heading1'], fontName='Helvetica-Bold',
        fontSize=14, leading=18, textColor=colors.HexColor('#005293'),
    ))
    styles.add(ParagraphStyle(
        'LocalProposalBody', parent=styles['BodyText'], fontName='Helvetica',
        fontSize=9.5, leading=13.5, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        'LocalProposalCitation', parent=styles['BodyText'], fontName='Helvetica-Oblique',
        fontSize=7.5, leading=9.5, textColor=colors.HexColor('#475569'), spaceAfter=8,
    ))
    document = SimpleDocTemplate(
        stream, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
    )
    story = [
        Spacer(1, 45 * mm),
        Paragraph(escape(content.get('title') or 'Technical Proposal'), styles['LocalProposalTitle']),
        Spacer(1, 12 * mm),
        Paragraph(escape(f"{content.get('client_name') or 'Client'} | {content.get('opportunity_reference') or ''}"),
                  styles['Heading2']),
        Paragraph(escape(
            f"Proposal {content.get('proposal_number') or ''} | Workspace revision {content.get('revision') or row.version}"
        ), styles['BodyText']),
        PageBreak(),
    ]
    for section_index, proposal_section in enumerate(content.get('sections') or []):
        story.append(Paragraph(
            escape(proposal_section.get('title') or 'Section'),
            styles['LocalProposalHeading'],
        ))
        for paragraph in proposal_section.get('paragraphs') or []:
            text = str(paragraph.get('text') or '').strip()
            if text:
                story.append(Paragraph(escape(text).replace('\n', '<br/>'), styles['LocalProposalBody']))
            item = next((
                value for value in proposal_section.get('items') or []
                if value.get('id') == paragraph.get('id')
            ), None)
            if item and item.get('citation'):
                story.append(Paragraph(
                    f'<i>Source: {escape(str(item["citation"]))}</i>',
                    styles['LocalProposalCitation'],
                ))
        if proposal_section.get('rows'):
            table_data = [
                [Paragraph(escape(str(value)).replace('\n', '<br/>'), styles['LocalProposalBody'])
                 for value in row]
                for row in proposal_section['rows']
            ]
            table = Table(table_data, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#CBD5E1')),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#005293')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story += [Spacer(1, 3 * mm), table]
        if section_index < len(content.get('sections') or []) - 1:
            story.append(PageBreak())
    document.build(story)
    return stream.getvalue(), _filename(quote, row, 'pdf')
