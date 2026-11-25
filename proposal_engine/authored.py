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
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
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
LIST_STEP = Pt(18)
ALIGNMENT = {'left': WD_ALIGN_PARAGRAPH.LEFT, 'center': WD_ALIGN_PARAGRAPH.CENTER,
             'right': WD_ALIGN_PARAGRAPH.RIGHT, 'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}
LIST_MARKS = ('\u2022', '\u2013', '\u25aa')
GENERATED_KINDS = ('cover', 'letter', 'compliance')


# --- reviewer formatting ----------------------------------------------------------------------
# Blocks keep their plain `text` (traceability, search, validation). Optional formatting set by a
# reviewer in the canvas editor: `runs` (character formatting), `align`, `indent` {left, first} in
# points; bullets: `markers` ('bullet'|'number'), `levels` (0-2), `item_runs`; tables: `header`,
# `column_runs`, `cell_runs` {"row:column": runs}.

def _shade_run(run, fill):
    properties = run._r.get_or_add_rPr()
    shading = OxmlElement('w:shd')
    shading.set(qn('w:val'), 'clear')
    shading.set(qn('w:color'), 'auto')
    shading.set(qn('w:fill'), fill.lstrip('#'))
    properties.append(shading)


def _write_runs(paragraph, text, runs=None, *, upper=False, bold=None, size=None, color=None):
    for item in runs or [{'text': text}]:
        run = paragraph.add_run(item['text'].upper() if upper else item['text'])
        if bold or item.get('bold'):
            run.bold = True
        if item.get('italic'):
            run.italic = True
        if item.get('underline'):
            run.underline = True
        if item.get('strike'):
            run.font.strike = True
        if item.get('font'):
            run.font.name = item['font']
            run._r.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), item['font'])
        if item.get('size') or size:
            run.font.size = Pt(item.get('size') or size.pt)
        if item.get('color'):
            run.font.color.rgb = RGBColor.from_string(item['color'].lstrip('#'))
        elif color is not None:
            run.font.color.rgb = color
        if item.get('highlight'):
            _shade_run(run, item['highlight'])
    return paragraph


def _format(paragraph, block):
    if block and block.get('align') in ALIGNMENT:
        paragraph.alignment = ALIGNMENT[block['align']]
    indent = (block or {}).get('indent') or {}
    if indent:
        paragraph.paragraph_format.left_indent = Pt(indent.get('left') or 0)
        paragraph.paragraph_format.first_line_indent = Pt(indent.get('first') or 0)
    return paragraph


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


def _body(document, text, block=None):
    paragraph = _spacing(document.add_paragraph())
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    _write_runs(paragraph, text, (block or {}).get('runs'))
    return _format(paragraph, block)


def _plain_heading(document, text, size=12, block=None):
    paragraph = _spacing(document.add_paragraph(), Pt(12), BODY_SPACING)
    color = _heading_color(document) or RGBColor(0x00, 0x00, 0xFF)
    _write_runs(paragraph, text, (block or {}).get('runs'), upper=True, bold=True, size=Pt(size), color=color)
    paragraph.paragraph_format.keep_with_next = True
    return _format(paragraph, block)


def _heading(document, text, level, numbered, block=None):
    style = _style(document, f'Heading {level}') if numbered else None
    if style:
        paragraph = document.add_paragraph(style=style)
        _write_runs(paragraph, text, (block or {}).get('runs'), upper=True)
        paragraph.paragraph_format.keep_with_next = True
        if level >= 3:  # the template indents Heading 3; the baseline starts it at the margin
            paragraph.paragraph_format.left_indent = Pt(0)
            paragraph.paragraph_format.first_line_indent = Pt(0)
        return _format(paragraph, block)
    return _plain_heading(document, text, 13 if level == 1 else 11, block)


def _bullets(document, items, block=None):
    block = block or {}
    markers, levels, item_runs = block.get('markers') or [], block.get('levels') or [], block.get('item_runs') or []
    counters = [0, 0, 0]
    for index, item in enumerate(items):
        level = levels[index] if index < len(levels) else 0
        numbered = (markers[index] if index < len(markers) else 'bullet') == 'number'
        counters[level] += 1
        for deeper in range(level + 1, len(counters)):
            counters[deeper] = 0
        mark = f'{counters[level]}.' if numbered else LIST_MARKS[level]
        paragraph = document.add_paragraph()
        paragraph.alignment = ALIGNMENT.get(block.get('align'), WD_ALIGN_PARAGRAPH.JUSTIFY)
        layout = paragraph.paragraph_format
        indent = BULLET_INDENT + LIST_STEP * level
        layout.left_indent, layout.first_line_indent = indent, -BULLET_HANG
        layout.tab_stops.add_tab_stop(indent)
        _spacing(paragraph, Pt(2) if index else Pt(4), Pt(4))
        paragraph.add_run(mark + '\t')
        _write_runs(paragraph, item, item_runs[index] if index < len(item_runs) else None)


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


def _cell_text(cell, text, bold=False, runs=None):
    cell.text = ''
    paragraph = cell.paragraphs[0]
    _spacing(paragraph, Pt(2), Pt(2))
    _write_runs(paragraph, str(text), runs, bold=bold or None, size=TABLE_FONT)


def _table(document, columns, rows, block=None):
    block = block or {}
    header = block.get('header', True) is not False
    column_runs, cell_runs = block.get('column_runs') or [], block.get('cell_runs') or {}
    width = max([len(columns)] + [len(row) for row in rows]) or 1
    table = document.add_table(rows=1 if header else 0, cols=width)
    style = _style(document, 'Table Grid')
    if style:
        table.style = style
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = _column_widths(document, columns if header else [], rows, width)
    if header:
        for index, (cell, text) in enumerate(zip(table.rows[0].cells, columns + [''] * (width - len(columns)))):
            _cell_text(cell, text, bold=True, runs=column_runs[index] if index < len(column_runs) else None)
            _shade(cell, TABLE_HEADER_FILL)
        repeat_header_row(table)
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for column, (cell, text) in enumerate(zip(cells, list(row) + [''] * (width - len(row)))):
            _cell_text(cell, text, runs=cell_runs.get(f'{row_index}:{column}'))
    _fix_widths(table, widths)
    _spacing(document.add_paragraph(), Pt(0), Pt(4))
    return table


def write_blocks(document, blocks, *, numbered):
    for block in blocks:
        kind = block.get('type')
        intro = {key: block[key] for key in ('runs', 'align', 'indent') if key in block}
        if kind == 'part':
            _heading(document, block['text'], 1, numbered)
        elif kind == 'heading':
            _heading(document, block['text'], int(block.get('level') or 2), numbered, block)
        elif kind == 'paragraph' and block.get('text'):
            _body(document, block['text'], block)
        elif kind == 'bullets' and block.get('items'):
            if block.get('text'):
                _body(document, block['text'], intro)
            _bullets(document, block['items'], block)
        elif kind == 'table' and block.get('rows'):
            if block.get('text'):
                _body(document, block['text'], intro)
            _table(document, list(block.get('columns') or []), block['rows'], block)
        elif kind == 'placeholder':
            paragraph = _spacing(document.add_paragraph())
            run = paragraph.add_run(block['text'])
            run.italic = True


def contents_entries(blocks):
    """(number, title, level) for each part and heading, numbered as the heading styles number them."""
    counters, entries = [0, 0, 0], []
    for block in blocks or []:
        if block.get('type') == 'part':
            level = 1
        elif block.get('type') == 'heading':
            level = max(1, min(3, int(block.get('level') or 2)))
        else:
            continue
        counters[level - 1] += 1
        for deeper in range(level, 3):
            counters[deeper] = 0
        title = ' '.join(str(block.get('text') or '').split()).upper()
        if title:
            entries.append(('.'.join(str(value) for value in counters[:level]), title, level))
    return entries


def _contents(document, title, entries, pages):
    """A section's own table of contents (as in the baseline method statement), then a page break."""
    for text in (title, 'TABLE OF CONTENTS'):
        paragraph = _spacing(document.add_paragraph(), Pt(6), Pt(6))
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(text)
        run.bold, run.font.size = True, Pt(12)
    width = _usable_width(document)
    for index, (number, text, level) in enumerate(entries):
        paragraph = _spacing(document.add_paragraph(), Pt(3 if level == 1 else 0), Pt(1))
        layout = paragraph.paragraph_format
        layout.left_indent = Pt(12 * (level - 1))
        layout.tab_stops.add_tab_stop(Pt(12 * (level - 1) + 36))
        layout.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        page = (pages or {}).get(index)
        run = paragraph.add_run(f'{number}\t{text}\t{page or ""}')
        run.font.size = Pt(9 if level > 1 else 10)
        run.bold = level == 1
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _has_contents(section, content):
    return bool(section.get('contents_title') and section.get('numbered_headings')
                and len(contents_entries((content or {}).get('blocks'))) >= 3)


def section_document(config, section, content, quote_ref, path, pages=None):
    # The section title is carried by its divider page (PDF) and section heading (Word master).
    document = _body_document(config, quote_ref)
    if _has_contents(section, content):
        _contents(document, section['contents_title'], contents_entries(content.get('blocks')), pages)
    write_blocks(document, content.get('blocks') or [], numbered=bool(section.get('numbered_headings')))
    document.save(str(path))
    return path


def heading_pages(pdf, entries):
    """{entry index: page number} where each contents entry's heading starts (first match after
    the contents pages, in order). Text is compared without spaces or punctuation, so wrapped
    headings and heading numbers do not matter."""
    import pymupdf

    def key(text):
        return re.sub(r'[^A-Z0-9]', '', text.upper())

    with pymupdf.open(str(pdf)) as document:
        texts = [key(page.get_text()) for page in document]
    if not entries:
        return {}
    last = key(entries[-1][1])[:40]
    start = next((index + 1 for index, text in enumerate(texts) if last and last in text), 0)
    pages, cursor = {}, start
    for index, (_, title, _) in enumerate(entries):
        wanted = key(title)[:40]
        found = next((page for page in range(cursor, len(texts)) if wanted and wanted in texts[page]), None)
        if found is not None:
            pages[index] = found + 1
            cursor = found
    return pages


def _paged_section_document(config, section, content, quote_ref, path, converter):
    """Write the section; when it has its own contents, render once to find the heading pages."""
    path = section_document(config, section, content, quote_ref, path)
    if not _has_contents(section, content):
        return path
    try:
        pages = heading_pages(converter.to_pdf(path), contents_entries(content.get('blocks')))
    except Exception:   # a converter problem must not stop the build; the contents keep no numbers
        return path
    return section_document(config, section, content, quote_ref, path, pages=pages)


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


def letter_file_blocks(path):
    """The text of a covering letter document (DOCX) as editable blocks, in document order."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    blocks = []
    for child in document.element.body.iterchildren():
        if child.tag == qn('w:p'):
            paragraph = Paragraph(child, document)
            text = ' '.join(paragraph.text.split())[:4000]
            if not text:
                continue
            block = {'type': 'paragraph', 'text': text}
            runs = [run for run in paragraph.runs if run.text.strip()]
            if runs and all(run.bold for run in runs):
                block['runs'] = [{'text': text, 'bold': True}]
            blocks.append(block)
        elif child.tag == qn('w:tbl'):
            rows = []
            for row in Table(child, document).rows:
                cells, seen = [], set()
                for cell in row.cells:
                    if id(cell._tc) in seen:   # merged cells repeat
                        continue
                    seen.add(id(cell._tc))
                    cells.append(' '.join(cell.text.split())[:600])
                if any(cells):
                    rows.append(cells[:8])
            if rows:
                width = max(len(row) for row in rows)
                rows = [row + [''] * (width - len(row)) for row in rows]
                blocks.append({'type': 'table', 'text': '', 'header': False, 'columns': [''] * width, 'rows': rows})
    return blocks[:400]


def cover_blocks(facts):
    """The cover's text as editable blocks (used when a reviewer replaces the designed cover)."""
    def line(text, size):
        return {'type': 'paragraph', 'text': text, 'align': 'center', 'runs': [{'text': text, 'size': size, 'bold': True}]}
    return [line('PROPOSAL', 14), line(facts['quote_ref'], 20), line(facts['tender_title'].upper(), 16),
            line(f"REFERENCE NUMBER {facts.get('rft_number') or 'TO BE CONFIRMED'}", 12),
            line('TECHNICAL PROPOSAL', 14), line(facts['revision_line'], 12),
            line(f"SUBMITTED TO {facts['client'].upper()}", 12)]


def letter_blocks(config, facts):
    """The covering letter as editable blocks, matching `letter_document`."""
    company = config['company']
    validity = facts.get('validity_days')
    validity_sentence = (f'Our offer is valid for {validity} days from the Technical Bid Submission date.' if validity
                         else 'Our offer is valid for the period required by the tender.')
    rows = [['From:', f"{company['signatory']}, {company['name']}"], ['Email:', company['contact_email']],
            ['Telephone:', company['telephone']], ['To:', facts['client']],
            ['Your Ref.:', facts.get('rft_number') or 'To be confirmed'],
            ['Our Ref.:', f"{facts['quote_ref']} Rev. {facts['revision']} Technical Proposal"],
            ['Date:', facts['date']], ['Subject:', f"{facts['tender_title'].upper()} – TECHNICAL PROPOSAL"]]
    blocks = [{'type': 'table', 'text': '', 'header': False, 'columns': ['', ''], 'rows': rows,
               'cell_runs': {f'{index}:0': [{'text': row[0], 'bold': True}] for index, row in enumerate(rows)}}]
    blocks += [{'type': 'paragraph', 'text': text.format(tender_title=facts['tender_title'],
                                                         validity_sentence=validity_sentence)}
               for text in config['letter']['paragraphs']]
    signatory = [company['signatory']] + list(company['signatory_title'])
    blocks += [{'type': 'paragraph', 'text': line, 'align': 'left',
                **({'runs': [{'text': line, 'bold': True}]} if index == 0 else {})} for index, line in enumerate(signatory)]
    return blocks


def _part_lines(document, header):
    """Header or footer paragraphs of the first section (tables included), in order."""
    part = document.sections[0].header if header else document.sections[0].footer
    return list(part._element.iter(qn('w:p')))


def _paragraph_text(element):
    return ''.join(node.text or '' for node in element.iter(qn('w:t')))


def template_layout(config):
    """Editable header and footer lines of the body template (the quote reference stays automatic)."""
    document = open_template(config['templates']['body'])
    token = config['templates']['quote_token']
    header = []
    for element in _part_lines(document, True):
        text = _paragraph_text(element).strip()
        if text and token not in text and text not in header:
            header.append(text)
    footer = [text for text in (_paragraph_text(e).strip() for e in _part_lines(document, False)) if text]
    return {'header_lines': header, 'footer_lines': footer}


def _replace_lines(path, pairs, target):
    """Copy a template with whole header/footer lines replaced (exact paragraph matches only)."""
    document = open_template(path)
    for package_part in document.part.package.iter_parts():
        if not re.search(r'/word/(header|footer)\d*\.xml$', str(package_part.partname)) or not hasattr(package_part, 'element'):
            continue
        for element in package_part.element.iter(qn('w:p')):
            text = _paragraph_text(element).strip()
            nodes = list(element.iter(qn('w:t')))
            if text not in pairs or not nodes:
                continue
            nodes[0].text = pairs[text]
            nodes[0].set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            for node in nodes[1:]:
                node.text = ''
    document.save(str(target))
    return str(target)


def apply_layout(config, layout, workdir):
    """Point the config at body/divider templates carrying the reviewer's header/footer lines."""
    defaults = template_layout(config)
    effective = {name: list(values) for name, values in defaults.items()}
    pairs = {}
    for name in ('header_lines', 'footer_lines'):
        for index, new in enumerate((layout or {}).get(name) or []):
            if index < len(defaults[name]) and isinstance(new, str) and new != defaults[name][index]:
                pairs[defaults[name][index]] = new
                effective[name][index] = new
    if pairs:
        for key in ('body', 'divider'):
            config['templates'][key] = _replace_lines(config['templates'][key], pairs, Path(workdir) / f'{key}_layout.docx')
    return effective


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


def render(config, facts, sections, requirements, traceability, converter, workdir, attachments=None, layout=None,
           letter=None):
    """Write every section document, compile the PDF binder and the Word master.

    `sections` maps section key → {'blocks': [...]} for authored/evidence/not-applicable sections;
    an entry may carry a reviewer `title`, and for cover/letter/compliance `override: True` replaces
    the generated page with its blocks. `attachments` maps an evidence section key → local files
    enclosed as that section. `layout` holds reviewer header/footer lines. `letter` ({'pdf', 'docx'}
    local paths) is the issued covering letter (the latest EOI from the Correspondence folder); when
    given it is the letter page instead of the template letter, unless a reviewer override applies.
    Returns {'pdf', 'docx', 'section_pages', 'findings', 'generated', 'layout'}.
    """
    from .binder import build_binder
    from .master import build_master

    workdir = Path(workdir)
    (workdir / 'sections').mkdir(parents=True, exist_ok=True)
    config = deepcopy(config)
    for section in config['sections']:
        title = (sections.get(section['key']) or {}).get('title')
        if isinstance(title, str) and title.strip():
            section['title'] = title.strip()
    effective_layout = apply_layout(config, layout, workdir)
    findings, files = [], {}
    labels = dict(review_units(config))
    generated_blocks = {}
    for section in config['sections']:
        key, kind = section['key'], section['kind']
        path = workdir / 'sections' / f'{len(files) + 1:02d}-{key}.docx'
        if kind in GENERATED_KINDS:
            generated_blocks[key] = (cover_blocks(facts) if kind == 'cover' else compliance_content(
                requirements, traceability, labels)['blocks'] if kind == 'compliance' else
                letter_file_blocks(letter['docx']) if letter and letter.get('docx') else letter_blocks(config, facts))
        if kind == 'letter' and letter and not (sections.get(key) or {}).get('override'):
            # The issued letter keeps its own look: its PDF in the binder, its DOCX in the Word file.
            files[key] = [letter.get('pdf') or letter['docx']]
            continue
        if kind in GENERATED_KINDS and (sections.get(key) or {}).get('override'):
            files[key] = section_document(config, section, sections[key], facts['quote_ref'], path)
        elif kind == 'cover':
            files[key] = cover_document(config, facts, path)
        elif kind == 'letter':
            files[key] = letter_document(config, facts, path)
        elif kind == 'compliance':
            files[key] = section_document(config, section, compliance_content(requirements, traceability, labels),
                                          facts['quote_ref'], path)
        elif kind == 'evidence' and (attachments or {}).get(key):
            files[key] = list(attachments[key])
        elif kind == 'authored' and (attachments or {}).get(key) and section.get('evidence_mode') == 'replace':
            files[key] = list(attachments[key])     # e.g. the signed qualification statement
        elif kind == 'authored' and (attachments or {}).get(key):
            # Drafted text first, then the documents the reviewer enclosed.
            files[key] = [_paged_section_document(config, section, sections.get(key) or {}, facts['quote_ref'], path,
                                                  converter), *attachments[key]]
        elif kind in ('authored', 'evidence', 'not_applicable'):
            files[key] = _paged_section_document(config, section, sections.get(key) or {}, facts['quote_ref'], path,
                                                 converter)
    config['_title'] = f"{facts['quote_ref']} Technical Proposal Rev {facts['revision']}"
    plan = build_plan(config, files)
    pdf = workdir / 'proposal.pdf'
    page_map = build_binder(plan, config, converter, workdir, pdf, findings)
    if letter and letter.get('pdf') and letter.get('docx'):
        for entry in plan:
            for component in entry['components']:
                if any(row['path'] == str(Path(letter['pdf'])) for row in component['files']):
                    component['files'] = [_row(Path(letter['docx']), entry['key'])]
    docx = workdir / 'proposal.docx'
    build_master(plan, config, {'facts': {'quote_ref': {'value': facts['quote_ref']}}}, {}, [], docx, findings,
                 annex_index=False)
    section_pages = {}
    for item in page_map:
        section_pages.setdefault(item['section'], [item['page'], item['page']])
        section_pages[item['section']][1] = item['page']
    generated = [path for path in files.values() if not isinstance(path, list)]
    findings.extend(_reference_leaks(generated, facts['quote_ref']))
    return {'pdf': pdf, 'docx': docx, 'section_pages': section_pages, 'findings': findings,
            'generated': generated_blocks, 'layout': effective_layout}


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
