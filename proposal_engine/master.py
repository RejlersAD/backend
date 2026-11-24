"""Editable master DOCX built on the curated company template."""
from __future__ import annotations

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from .docxkit import (DocxMerger, add_field, add_page_break, ensure_paragraph_style, open_template,
                      repeat_header_row, replace_text)
from .extract import source_rows

SECTION_STYLES = ('Proposal Section 1', 'Proposal Section 2', 'Proposal Section 3')


def _editable(component, selections, files):
    """Prefer an editable DOCX (selected file or its twin) for each selected file."""
    out = []
    for row in component['files']:
        twins = [r for r in source_rows({component['key']: ([row], [])}, component['key'], files) if r['ext'] == 'docx']
        out.append((row, twins[0] if twins else None))
    return out


def _placeholder(document, row, pages):
    table = document.add_table(rows=1, cols=1)
    table.style = 'Table Grid'
    cell = table.rows[0].cells[0]
    cell.text = ''
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run('Annex document (not editable in this file)')
    run.bold = True
    detail = cell.add_paragraph(f"{row['name']} — {row['ext'].upper()}")
    if pages:
        detail.add_run(f" — included verbatim in the client PDF binder, pages {pages[0]}–{pages[1]}.")
    detail.runs[0].font.size = Pt(9)
    cell.add_paragraph(f"Source: {row['rel']}").runs[0].font.size = Pt(8)


def build_master(plan, config, facts, selections, files, out_docx, findings, annex_index=True):
    document = open_template(config['templates']['body'])
    quote = (facts['facts'].get('quote_ref') or {}).get('value') or '[QUOTE REF MISSING]'
    replace_text(document, config['templates']['quote_token'], quote)
    for index, (name, size) in enumerate(zip(SECTION_STYLES, (16, 13, 11))):
        ensure_paragraph_style(document, name, index, size)

    section = document.sections[0]
    footer = section.footer
    paragraph = footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.add_run('Page ')
    add_field(paragraph, 'PAGE', '1')
    paragraph.add_run(' of ')
    add_field(paragraph, 'NUMPAGES', '1')
    for run in paragraph.runs:
        run.font.size = Pt(8)

    merger = DocxMerger(document)
    page_breaks_before = False
    for entry in plan:
        if entry['generated'] == 'toc':
            if page_breaks_before:
                add_page_break(document)
            document.add_paragraph('TABLE OF CONTENTS', style=SECTION_STYLES[0])
            styles = ','.join(f'{name},{level + 1}' for level, name in enumerate(SECTION_STYLES[:2]))
            add_field(document.add_paragraph(), f'TOC \\h \\z \\t "{styles}"',
                      'Right-click and choose Update Field to build the table of contents.')
            merger.close_letterhead_section()  # TOC page keeps the letterhead header
            page_breaks_before = False
            continue
        if entry['number'] or entry['subdivider']:
            if page_breaks_before:
                add_page_break(document)
            style = SECTION_STYLES[2] if entry['subdivider'] else SECTION_STYLES[min(entry['depth'], 2) - 1]
            document.add_paragraph(entry['label'], style=style)
            page_breaks_before = False
        for component in entry['components']:
            for row, editable in _editable(component, selections, files):
                pages = next(((a, b) for a, b, rel in component.get('binder_pages') or [] if rel == row['rel']), None)
                if page_breaks_before:
                    add_page_break(document)
                if editable is not None and not component.get('pages'):
                    try:
                        # Submitted documents (cover, letter) keep their own look: no letterhead header.
                        merger.append(editable['path'], letterhead=component['role'] != 'submitted')
                        page_breaks_before = False  # the component's section break starts a new page
                        continue
                    except Exception as error:
                        findings.append(('ERROR', 'MERGE_FAILED', f"{editable['rel']}: {error}"))
                _placeholder(document, row, pages)
                page_breaks_before = True

    if annex_index:
        _annex_index(document, plan)
    for item in sorted(set(merger.dropped)):
        findings.append(('WARN', 'EMBEDDED_OBJECT_DROPPED', f'{item} (not editable in master DOCX; present in client PDF)'))
    settings = document.settings.element
    update = OxmlElement('w:updateFields')
    update.set(qn('w:val'), 'true')
    settings.append(update)
    document.save(str(out_docx))


def _annex_index(document, plan):
    add_page_break(document)
    document.add_paragraph('ANNEX INDEX', style=SECTION_STYLES[0])
    document.add_paragraph('Documents compiled in the client PDF binder, in binder order. '
                           'Full source paths are listed in build_report.md and page_map.json.')
    headers = ['No.', 'Section', 'Document', 'Format', 'Binder pages']
    table = document.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    table.autofit = False
    widths = [Cm(1.0), Cm(5.2), Cm(6.6), Cm(1.6), Cm(2.2)]
    for cell, text, width in zip(table.rows[0].cells, headers, widths):
        cell.text = text
        cell.width = width
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].runs[0].font.size = Pt(8)
    repeat_header_row(table)
    number = 0
    for entry in plan:
        for component in entry['components']:
            for start, end, rel in component.get('binder_pages') or []:
                number += 1
                cells = table.add_row().cells
                for cell, text, width in zip(cells, [str(number), entry['label'], rel.rsplit('/', 1)[-1],
                                                     rel.rsplit('.', 1)[-1].upper(), f'{start}–{end}'], widths):
                    cell.text = text
                    cell.width = width
                    for run in cell.paragraphs[0].runs:
                        run.font.size = Pt(8)
