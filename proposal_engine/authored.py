"""Render an authored technical proposal (DEC-TP-001) into one PDF deliverable and a Word master.

Framework-independent: input is the proposal content (sections with cited blocks) plus facts; the
company standard comes from `config/rejlers_technical_proposal.yaml` and the curated templates.
"""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

import yaml
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor

from .docxkit import clear_body, fill_tokens, open_template, repeat_header_row, replace_text

DEFAULT_AUTHORED_CONFIG = Path(__file__).parent / 'config' / 'rejlers_technical_proposal.yaml'
TEMPLATE_KEYS = ('body', 'divider', 'cover', 'standard_content')


def load_authored_config(path=None):
    path = Path(path or DEFAULT_AUTHORED_CONFIG)
    with open(path, encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    for key in TEMPLATE_KEYS:
        config['templates'][key] = str((path.parent / config['templates'][key]).resolve())
    config['templates']['stamp']['image'] = str((path.parent / config['templates']['stamp']['image']).resolve())
    keys = [section['key'] for section in config['sections']]
    if len(keys) != len(set(keys)):
        raise ValueError(f'{path}: duplicate section keys')
    config['_path'] = str(path)
    return config


def section_label(section):
    number = section.get('number')
    title = section['title'].upper()
    return f'{number} {title}' if number else title


def review_units(config):
    """Sections a reviewer approves, in document order: everything except the TOC and pure dividers."""
    return [(section['key'], section_label(section)) for section in config['sections']
            if section['kind'] not in ('toc', 'divider')]


# --- section documents ----------------------------------------------------------------------

# Rejlers house style (approved Execution Plan, Q-101505 Rev 0): Arial, justified body text with
# 6 pt spacing, round bullets on a hanging indent, blue bold uppercase headings, compact tables.
BODY_SPACING = Pt(6)
BULLET_INDENT, BULLET_HANG = Pt(36), Pt(18)
TABLE_HEADER_FILL = 'DCE6F1'
TABLE_FONT = Pt(10)


def _arial_numbering(document):
    """Heading numbers inherit the theme font (a serif) unless the numbering level names one."""
    try:
        numbering = document.part.numbering_part.element
    except (KeyError, NotImplementedError):
        return
    for level in numbering.iter(qn('w:lvl')):
        if (level.find(qn('w:numFmt')) is not None
                and level.find(qn('w:numFmt')).get(qn('w:val')) == 'bullet'):
            continue
        rpr = level.find(qn('w:rPr'))
        if rpr is None:
            rpr = OxmlElement('w:rPr')
            level.append(rpr)
        fonts = rpr.find(qn('w:rFonts'))
        if fonts is None:
            fonts = OxmlElement('w:rFonts')
            rpr.insert(0, fonts)
        for attribute in ('w:asciiTheme', 'w:hAnsiTheme', 'w:cstheme', 'w:eastAsiaTheme'):
            fonts.attrib.pop(qn(attribute), None)  # theme fonts override the named font
        for attribute in ('w:ascii', 'w:hAnsi', 'w:cs'):
            fonts.set(qn(attribute), 'Arial')
        for size in ('w:sz', 'w:szCs'):  # the number takes the heading's own size
            for element in rpr.findall(qn(size)):
                rpr.remove(element)


def _body_document(config, quote_ref):
    document = open_template(config['templates']['body'])
    clear_body(document)
    replace_text(document, config['templates']['quote_token'], quote_ref)
    _arial_numbering(document)
    return document


def _style(document, *names):
    available = {style.name for style in document.styles}
    return next((name for name in names if name in available), None)


def _heading_color(document):
    try:
        color = document.styles['Heading 1'].font.color
        return color.rgb if color is not None and color.type is not None else None
    except KeyError:
        return None


def _spacing(paragraph, before=BODY_SPACING, after=BODY_SPACING):
    paragraph.paragraph_format.space_before = before
    paragraph.paragraph_format.space_after = after
    return paragraph


def _body(document, text):
    paragraph = _spacing(document.add_paragraph(text))
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return paragraph


def _plain_heading(document, text, size=12):
    paragraph = _spacing(document.add_paragraph(), Pt(12), BODY_SPACING)
    run = paragraph.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(size)
    color = _heading_color(document)
    run.font.color.rgb = color or RGBColor(0x00, 0x00, 0xFF)
    paragraph.paragraph_format.keep_with_next = True
    return paragraph


def _heading(document, text, level, numbered):
    style = _style(document, f'Heading {level}') if numbered else None
    if style:
        paragraph = document.add_paragraph(text.upper(), style=style)
        paragraph.paragraph_format.keep_with_next = True
        if level >= 3:  # the template indents Heading 3; the baseline starts it at the margin
            paragraph.paragraph_format.left_indent = Pt(0)
            paragraph.paragraph_format.first_line_indent = Pt(0)
        return paragraph
    return _plain_heading(document, text, 13 if level == 1 else 11)


def _bullets(document, items):
    for index, item in enumerate(items):
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        layout = paragraph.paragraph_format
        layout.left_indent, layout.first_line_indent = BULLET_INDENT, -BULLET_HANG
        layout.tab_stops.add_tab_stop(BULLET_INDENT)
        _spacing(paragraph, Pt(2) if index else Pt(4), Pt(4))
        paragraph.add_run('•\t' + item)


def _usable_width(document):
    section = document.sections[-1]
    try:
        return section.page_width - section.left_margin - section.right_margin
    except TypeError:
        return Emu(int(6.3 * 914400))


def _column_widths(document, columns, rows, width):
    """Share the usable width by content length; short index columns stay narrow."""
    total = _usable_width(document)
    weights = []
    for index in range(width):
        texts = [str(columns[index]) if index < len(columns) else ''] + [
            str(row[index]) for row in rows if index < len(row)]
        longest = max((len(text) for text in texts), default=1)
        average = sum(len(text) for text in texts) / max(len(texts), 1)
        weights.append(max(4.0, min(60.0, average * 0.7 + longest * 0.3)))
    share = sum(weights)
    return [Emu(int(total * weight / share)) for weight in weights]


def _fix_widths(table, widths):
    """Fixed layout with grid and cell widths: LibreOffice and Word both honour the grid."""
    properties = table._tbl.tblPr
    layout = properties.find(qn('w:tblLayout'))
    if layout is None:
        layout = OxmlElement('w:tblLayout')
        properties.append(layout)
    layout.set(qn('w:type'), 'fixed')
    for column, width in zip(table.columns, widths):
        column.width = width
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = width


def _shade(cell, fill):
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement('w:shd')
    shading.set(qn('w:val'), 'clear')
    shading.set(qn('w:color'), 'auto')
    shading.set(qn('w:fill'), fill)
    properties.append(shading)


def _cell_text(cell, text, bold=False):
    cell.text = ''
    paragraph = cell.paragraphs[0]
    _spacing(paragraph, Pt(2), Pt(2))
    run = paragraph.add_run(str(text))
    run.bold = bold
    run.font.size = TABLE_FONT


def _table(document, columns, rows):
    width = max([len(columns)] + [len(row) for row in rows]) or 1
    table = document.add_table(rows=1, cols=width)
    style = _style(document, 'Table Grid')
    if style:
        table.style = style
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = _column_widths(document, columns, rows, width)
    for cell, text in zip(table.rows[0].cells, columns + [''] * (width - len(columns))):
        _cell_text(cell, text, bold=True)
        _shade(cell, TABLE_HEADER_FILL)
    repeat_header_row(table)
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, list(row) + [''] * (width - len(row))):
            _cell_text(cell, text)
    _fix_widths(table, widths)
    _spacing(document.add_paragraph(), Pt(0), Pt(4))
    return table


def write_blocks(document, blocks, *, numbered):
    for block in blocks:
        kind = block.get('type')
        if kind == 'part':
            _heading(document, block['text'], 1, numbered)
        elif kind == 'heading':
            _heading(document, block['text'], int(block.get('level') or 2), numbered)
        elif kind == 'paragraph' and block.get('text'):
            _body(document, block['text'])
        elif kind == 'bullets' and block.get('items'):
            if block.get('text'):
                _body(document, block['text'])
            _bullets(document, block['items'])
        elif kind == 'table' and block.get('rows'):
            if block.get('text'):
                _body(document, block['text'])
            _table(document, list(block.get('columns') or []), block['rows'])
        elif kind == 'placeholder':
            paragraph = _spacing(document.add_paragraph())
            run = paragraph.add_run(block['text'])
            run.italic = True


def section_document(config, section, content, quote_ref, path):
    # The section title is carried by its divider page (PDF) and section heading (Word master).
    document = _body_document(config, quote_ref)
    write_blocks(document, content.get('blocks') or [], numbered=bool(section.get('numbered_headings')))
    document.save(str(path))
    return path


def cover_document(config, facts, path):
    return fill_tokens(config['templates']['cover'], path, {
        'quote_ref': facts['quote_ref'], 'tender_title': facts['tender_title'].upper(),
        'rft_number': facts.get('rft_number') or 'TO BE CONFIRMED', 'revision_line': facts['revision_line'],
        'client': facts['client'].upper(),
    })


def letter_document(config, facts, path):
    document = _body_document(config, facts['quote_ref'])
    company = config['company']
    rows = [('From:', f"{company['signatory']}, {company['name']}"), ('Email:', company['contact_email']),
            ('Telephone:', company['telephone']), ('To:', facts['client']),
            ('Your Ref.:', facts.get('rft_number') or 'To be confirmed'),
            ('Our Ref.:', f"{facts['quote_ref']} Rev. {facts['revision']} Technical Proposal"),
            ('Date:', facts['date']), ('Subject:', f"{facts['tender_title'].upper()} – TECHNICAL PROPOSAL")]
    table = document.add_table(rows=0, cols=2)
    table.autofit = False
    label_width = Emu(int(1.3 * 914400))
    for label, value in rows:
        cells = table.add_row().cells
        for cell, text, bold in ((cells[0], label, True), (cells[1], value, False)):
            cell.text = ''
            run = _spacing(cell.paragraphs[0], Pt(1), Pt(1)).add_run(text)
            run.bold = bold
    _fix_widths(table, [label_width, _usable_width(document) - label_width])
    _spacing(document.add_paragraph(), Pt(0), Pt(6))
    validity = facts.get('validity_days')
    validity_sentence = (f'Our offer is valid for {validity} days from the Technical Bid Submission date.' if validity
                         else 'Our offer is valid for the period required by the tender.')
    for text in config['letter']['paragraphs']:
        _body(document, text.format(tender_title=facts['tender_title'], validity_sentence=validity_sentence))
    # Room for the signature above the signatory block.
    _spacing(document.add_paragraph(), Pt(0), Pt(36))
    for index, line in enumerate([company['signatory']] + list(company['signatory_title'])):
        run = _spacing(document.add_paragraph(), Pt(0), Pt(0)).add_run(line)
        run.bold = index == 0
    document.save(str(path))
    return path


def compliance_content(requirements, traceability, labels):
    rows = []
    for index, requirement in enumerate(requirements, 1):
        links = traceability.get(requirement['id']) or []
        where = ', '.join(sorted({labels.get(key, key) for key in links})) or '—'
        source = Path(requirement.get('source_file') or '').name
        if requirement.get('page'):
            source += f", p. {requirement['page']}"
        rows.append([str(index), requirement['requirement_text'][:400], source,
                     where, 'Addressed' if links else 'To be confirmed'])
    return {'blocks': [
        {'type': 'paragraph', 'text': 'The table lists each requirement identified in the tender documents and the '
                                      'proposal section that responds to it.'},
        {'type': 'table', 'columns': ['No.', 'Tender requirement', 'Tender source', 'Response in', 'Status'], 'rows': rows},
    ]}


# --- assembly -------------------------------------------------------------------------------

def _row(path, key):
    path = Path(path)
    return {'path': str(path), 'rel': f'generated/{key}/{path.name}', 'name': path.name,
            'ext': path.suffix.lower().lstrip('.') or 'docx',
            'sha256': '', 'duplicate_of': None, 'variant': False, 'twin_of': None, 'supported': True,
            'components': [key], 'reference_only': None, 'foreign_refs': []}


def build_plan(config, files):
    """Binder plan (see proposal_engine.binder) over generated section documents."""
    plan = []
    for section in config['sections']:
        kind = section['kind']
        parent = section.get('parent')
        numbered = bool(section.get('number'))
        paths = files.get(section['key'])
        paths = paths if isinstance(paths, (list, tuple)) else [paths] if paths else []
        component = [] if not paths else [{
            'key': section['key'], 'files': [_row(path, section['key']) for path in paths], 'pages': None,
            'role': 'submitted' if kind in ('cover', 'letter') else 'authored'}]
        plan.append({
            'key': section['key'], 'title': section['title'], 'number': section.get('number'),
            'label': section_label(section), 'depth': 2 if parent else 1, 'parent': parent,
            'divider': numbered and kind not in ('cover', 'letter', 'toc'), 'subdivider': False,
            'required': False, 'generated': 'toc' if kind == 'toc' else None, 'toc': numbered,
            'not_applicable': kind == 'not_applicable', 'components': component,
        })
    return plan


def render(config, facts, sections, requirements, traceability, converter, workdir, attachments=None):
    """Write every section document, compile the PDF binder and the Word master.

    `sections` maps section key → {'blocks': [...]} for authored/evidence/not-applicable sections.
    `attachments` maps an evidence section key → local files (PDF/Office) enclosed as that section.
    Returns {'pdf', 'docx', 'section_pages', 'findings'}.
    """
    from .binder import build_binder
    from .master import build_master

    workdir = Path(workdir)
    (workdir / 'sections').mkdir(parents=True, exist_ok=True)
    findings, files = [], {}
    labels = dict(review_units(config))
    for section in config['sections']:
        key, kind = section['key'], section['kind']
        path = workdir / 'sections' / f'{len(files) + 1:02d}-{key}.docx'
        if kind == 'cover':
            files[key] = cover_document(config, facts, path)
        elif kind == 'letter':
            files[key] = letter_document(config, facts, path)
        elif kind == 'compliance':
            files[key] = section_document(config, section, compliance_content(requirements, traceability, labels),
                                          facts['quote_ref'], path)
        elif kind == 'evidence' and (attachments or {}).get(key):
            files[key] = list(attachments[key])
        elif kind in ('authored', 'evidence', 'not_applicable'):
            files[key] = section_document(config, section, sections.get(key) or {}, facts['quote_ref'], path)
    config = deepcopy(config)
    config['_title'] = f"{facts['quote_ref']} Technical Proposal Rev {facts['revision']}"
    plan = build_plan(config, files)
    pdf = workdir / 'proposal.pdf'
    page_map = build_binder(plan, config, converter, workdir, pdf, findings)
    docx = workdir / 'proposal.docx'
    build_master(plan, config, {'facts': {'quote_ref': {'value': facts['quote_ref']}}}, {}, [], docx, findings,
                 annex_index=False)
    section_pages = {}
    for item in page_map:
        section_pages.setdefault(item['section'], [item['page'], item['page']])
        section_pages[item['section']][1] = item['page']
    generated = [path for path in files.values() if not isinstance(path, list)]
    findings.extend(_reference_leaks(generated, facts['quote_ref']))
    return {'pdf': pdf, 'docx': docx, 'section_pages': section_pages, 'findings': findings}


def _reference_leaks(paths, quote_ref):
    """Other bids' quote references must never appear in a generated proposal."""
    from .readers import docx_parts

    own = re.sub(r'\D', '', quote_ref)
    leaks = set()
    for path in paths:
        for part in docx_parts(path).values():
            for value in re.findall(r'\bQ-?(\d{6})\b', part['text'] + part['hidden']):
                if value != own:
                    leaks.add(f'Q-{value}')
    return [('ERROR', 'LEGACY_REFERENCE', f'Generated text refers to another bid {ref}') for ref in sorted(leaks)]
