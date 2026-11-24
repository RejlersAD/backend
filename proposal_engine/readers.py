"""Format readers. Pure functions over file paths; no Django imports."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
NS = {'w': W, 'a': A, 'mc': MC}
TOC_STYLE = re.compile(r'^(toc\s*\d|toc heading|table of figures)', re.I)


def _parse(data):
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, huge_tree=True))


def _paragraph_texts(root):
    """Yield (text, hidden_text) per w:p, ignoring mc:Fallback copies of text boxes."""
    for paragraph in root.iter(f'{{{W}}}p'):
        if any(ancestor.tag == f'{{{MC}}}Fallback' for ancestor in paragraph.iterancestors()):
            continue
        visible, hidden = [], []
        for run in paragraph.iter(f'{{{W}}}r'):
            # Runs inside nested text-box paragraphs are reported by their own w:p.
            if next(run.iterancestors(f'{{{W}}}p'), None) is not paragraph:
                continue
            is_hidden = run.find('w:rPr/w:vanish', NS) is not None
            for node in run:
                if node.tag == f'{{{W}}}t':
                    (hidden if is_hidden else visible).append(node.text or '')
                elif node.tag in (f'{{{W}}}tab',):
                    visible.append('\t')
        yield ''.join(visible), ''.join(hidden)


def docx_parts(path):
    """Return {part_name: {'text': str, 'hidden': str}} for body, headers, footers and notes."""
    parts = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not re.match(r'word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$', name):
                continue
            root = _parse(archive.read(name))
            visible, hidden = [], []
            for text, hidden_text in _paragraph_texts(root):
                visible.append(text)
                if hidden_text:
                    hidden.append(hidden_text)
            parts[name] = {'text': '\n'.join(visible), 'hidden': '\n'.join(hidden)}
    return parts


def docx_body_blocks(path):
    """Ordered body blocks (paragraphs and tables), skipping Word TOC styles."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    blocks = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            paragraph = Paragraph(child, document)
            style = paragraph.style.name if paragraph.style is not None else ''
            text = paragraph.text.strip()
            if not text or TOC_STYLE.match(style):
                continue
            level = re.match(r'Heading\s*(\d)', style)
            blocks.append({'kind': 'heading' if level else 'paragraph', 'level': int(level.group(1)) if level else 0,
                           'style': style, 'text': text})
        elif tag == 'tbl':
            rows = []
            for row in Table(child, document).rows:
                values, previous = [], None
                for cell in row.cells:
                    value = ' '.join(cell.text.split())
                    values.append('' if cell._tc is previous else value)
                    previous = cell._tc
                rows.append(values)
            blocks.append({'kind': 'table', 'rows': rows, 'text': ''})
    return blocks


def pptx_text(path):
    slides = []
    with zipfile.ZipFile(path) as archive:
        names = sorted((n for n in archive.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)),
                       key=lambda n: int(re.search(r'(\d+)', n.rsplit('/', 1)[-1]).group(1)))
        for name in names:
            root = _parse(archive.read(name))
            slides.append(' '.join(node.text or '' for node in root.iter(f'{{{A}}}t')))
    return slides


def pdf_pages_text(path):
    import pymupdf

    with pymupdf.open(str(path)) as document:
        return [page.get_text() for page in document]


def spreadsheet_rows(path):
    suffix = Path(path).suffix.lower()
    sheets = {}
    if suffix == '.xlsx':
        from openpyxl import load_workbook

        workbook = load_workbook(str(path), read_only=True, data_only=True, keep_links=False)
        try:
            for sheet in workbook.worksheets:
                sheets[sheet.title] = [['' if v is None else str(v).strip() for v in row]
                                       for row in sheet.iter_rows(values_only=True)]
        finally:
            workbook.close()
    elif suffix == '.xls':
        import xlrd

        workbook = xlrd.open_workbook(str(path))
        for sheet in workbook.sheets():
            sheets[sheet.name] = [[str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
                                  for r in range(sheet.nrows)]
    return sheets


def file_text(path):
    """Searchable text of any supported file: {'text': str, 'hidden': str, 'locations': [(loc, text)]}."""
    suffix = Path(path).suffix.lower()
    locations, hidden = [], ''
    try:
        if suffix == '.docx':
            for name, part in docx_parts(path).items():
                locations.append((name, part['text']))
                if part['hidden']:
                    hidden += part['hidden'] + '\n'
                    locations.append((f'{name} (hidden)', part['hidden']))
        elif suffix == '.pptx':
            locations = [(f'slide {i}', text) for i, text in enumerate(pptx_text(path), 1)]
        elif suffix == '.pdf':
            locations = [(f'page {i}', text) for i, text in enumerate(pdf_pages_text(path), 1)]
        elif suffix in ('.xlsx', '.xls'):
            locations = [(f'sheet {name}', '\n'.join(' | '.join(row) for row in rows))
                         for name, rows in spreadsheet_rows(path).items()]
    except Exception as error:  # unreadable file is reported by validation, never guessed
        return {'text': '', 'hidden': '', 'locations': [], 'error': f'{type(error).__name__}: {error}'}
    return {'text': '\n'.join(text for _, text in locations), 'hidden': hidden, 'locations': locations}
